import io
import os
import tempfile
from pathlib import Path
import pandas as pd
import pytest
import xlwt
from fastapi.testclient import TestClient

sandbox = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = os.environ.get('PILOT_TEST_DATABASE_URL', 'sqlite:///' + str(Path(sandbox.name) / 'test.sqlite'))
os.environ['DISABLE_RATE_LIMIT'] = 'true'
os.environ['COOKIE_SECURE'] = 'false'
from app.main import app
from app.store import Session, User
from sqlalchemy import select

H = {'X-DataPilot':'1'}
CSV = b'Date,Region,Sales,Orders\n2026-01-01,West,10,2\n2026-01-02,East,20,4\n2026-02-01,West,,6\n2026-02-02,East,40,8\n2026-02-02,East,40,8\n,,,\n'


@pytest.fixture(scope='module',autouse=True)
def setup():
    with TestClient(app): yield


@pytest.fixture
def client(request):
    with TestClient(app,headers=H) as c:
        r=c.post('/api/auth/register',json={'email':request.node.name+'@example.org','name':'Test User','password':'fixture-only-0123'})
        assert r.status_code==201,r.text
        yield c


def add(c, name='test.csv', raw=CSV):
    r=c.post('/api/datasets',files={'file':(name,raw)})
    assert r.status_code==201,r.text
    return r.json()['id']


def test_registration_login_logout_and_account_hash(client):
    email='test_registration_login_logout_and_account_hash@example.org'
    assert client.get('/api/auth/me').json()['user']['email']==email
    with Session() as db:
        assert db.scalar(select(User).where(User.email==email)).password.startswith('$argon2id$')
    old=client.cookies.get('pilot_session')
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/dashboard').status_code==401
    with TestClient(app,headers=H) as other:
        other.cookies.set('pilot_session',old)
        assert other.get('/api/dashboard').status_code==401
    assert client.post('/api/auth/login',json={'email':email,'password':'wrong'}).status_code==401
    assert client.post('/api/auth/login',json={'email':email,'password':'fixture-only-0123'}).status_code==200
    assert client.patch('/api/auth/profile',json={'name':'Updated'}).json()['name']=='Updated'


def test_csrf_and_invalid_upload(client):
    with TestClient(app) as c:
        assert c.post('/api/auth/demo').status_code==403
    for name,raw,expected in [('test.exe',CSV,415),('test.csv',b'\x00\x01\x02',422),('test.xlsx',CSV,422),('test.xls',CSV,422),('header.csv',b'a,b',422),('malformed.csv',b'a,b\n1,2,3\n',422),('big.csv',b'o'*(8*1024*1024+1),413)]:
        r=client.post('/api/datasets',files={'file':(name,raw)})
        assert r.status_code==expected,r.text


def test_csv_preview_profile_and_search(client):
    id=add(client)
    r=client.get(f'/api/datasets/{id}').json()
    assert r['rows']==6 and r['column_count']==4 and r['duplicates']==1 and r['missing']==5 and r['empty']==1
    assert [c['type'] for c in r['columns']]==['Date','Categorical','Numeric','Numeric']
    assert len(r['preview'])==6 and r['memory']>0
    res=client.get(f'/api/datasets/{id}/records',params={'q':'west','column':'Region','operator':'equals'}).json()
    assert res['total']==2 and all(r[1]=='West' for r in res['rows'])
    assert client.get(f'/api/datasets/{id}/records',params={'column':'Sales','operator':'missing'}).json()['total']==2


@pytest.mark.parametrize('ext',['xls','xlsx'])
def test_excel_upload(client,ext):
    buf=io.BytesIO()
    if ext=='xls':
        w=xlwt.Workbook();s=w.add_sheet('data');s.write(0,0,'Amount');s.write(1,0,12);w.save(buf)
    else: pd.DataFrame({'Amount':[12]}).to_excel(buf,index=False)
    id=add(client,'workbook.'+ext,buf.getvalue())
    assert client.get(f'/api/datasets/{id}').json()['preview']==[[12]]


def test_clean_preview_apply_undo_reset_and_downloads(client):
    id=add(client)
    payload={'duplicates':True,'empty':True,'rules':[{'column':'Sales','method':'median','rename':'Revenue'},{'column':'Orders','cast':'text'}]}
    before=f'/api/datasets/{id}'
    preview=client.post(before+'/clean/preview',json=payload).json()
    assert preview['rows']==4 and preview['missing']==0
    assert client.get(before).json()['rows']==6
    assert client.post(before+'/clean/apply',json=payload).status_code==200
    assert client.post(before+'/clean/apply',json=payload).status_code==409
    assert client.get(before).json()['columns'][2]['name']=='Revenue'
    assert 'Revenue' in client.get(before+'/export/csv').text
    raw=client.get(before+'/export/xlsx').content
    assert pd.read_excel(io.BytesIO(raw))['Revenue'].tolist()==[10,20,30,40]
    assert client.post(before+'/clean/undo').status_code==200
    assert client.get(before).json()['preview'][2][2] is None
    assert client.post(before+'/clean/apply',json={**payload,'version':2}).status_code==200
    assert client.post(before+'/clean/reset').status_code==200
    assert client.get(before).json()['rows']==6
    assert client.post(before+'/clean/undo').status_code==200
    assert client.get(before).json()['rows']==4
    assert len(client.get('/api/history').json())==6


def test_analysis_and_insights(client):
    id=add(client)
    s=client.post(f'/api/datasets/{id}/analyze').json()
    sales=s['numeric'][0]
    assert sales['min']==10 and sales['max']==40 and sales['mean']==27.5 and sales['median']==30 and sales['mode']==40
    assert sales['q1']==17.5 and sales['q3']==40
    assert s['categorical'][0]['frequencies'][0]=={'name':'East','value':3}
    assert s['correlation']['values'][0][1]>0.99
    text=' '.join(i['title']+' '+i['body'] for i in s['insights'])
    assert 'West' in text and 'East' in text and 'correlation' in text and 'over time' in text
    assert any(i['category']=='Recommendation' for i in s['insights'])


@pytest.mark.parametrize('kind,x,y,count',[('bar','Region','Sales',2),('line','Date','Sales',3),('pie','Region','',2),('histogram','Sales','',10),('scatter','Sales','Orders',4),('heatmap','','',2)])
def test_charts(client,kind,x,y,count):
    id=add(client)
    r=client.get(f'/api/datasets/{id}/chart',params={'type':kind,'x':x,'y':y})
    assert r.status_code==200,r.text
    assert len(r.json()['values' if kind=='heatmap' else 'points'])==count


def test_pdf_docx_history_and_delete(client):
    id=add(client)
    r=client.post(f'/api/datasets/{id}/reports')
    assert r.status_code==201,r.text
    key=r.json()['id']
    assert client.get('/api/reports').json()[0]['id']==key
    assert client.get(f'/api/reports/{key}').json()['dataset_name']=='test.csv'
    pdf=client.get(f'/api/reports/{key}/file/pdf')
    assert pdf.content.startswith(b'%PDF-') and len(pdf.content)>10000
    assert 'inline' in client.get(f'/api/reports/{key}/file/pdf?inline=true').headers['content-disposition']
    word=client.get(f'/api/reports/{key}/file/docx')
    from docx import Document
    assert Document(io.BytesIO(word.content)).paragraphs[0].text=='DataPilot AI'
    assert client.delete(f'/api/datasets/{id}').status_code==200
    assert client.get(f'/api/reports/{key}').json()['dataset_id'] is None
    assert client.delete(f'/api/reports/{key}').status_code==200
    assert client.get('/api/reports').json()==[]


def test_isolation(client):
    id=add(client)
    rid=client.post(f'/api/datasets/{id}/reports').json()['id']
    with TestClient(app,headers=H) as c:
        r=c.post('/api/auth/register',json={'email':'second@example.org','name':'Other','password':'test-password'})
        assert r.status_code==201
        assert c.get('/api/datasets').json()==[] and c.get('/api/reports').json()==[] and c.get('/api/history').json()==[]
        for path in [f'/api/datasets/{id}',f'/api/datasets/{id}/export/csv',f'/api/reports/{rid}',f'/api/reports/{rid}/file/pdf']:
            assert c.get(path).status_code==404
        assert c.delete(f'/api/datasets/{id}').status_code==404
        assert c.post(f'/api/datasets/{id}/clean/apply',json={'duplicates':True}).status_code==404


def test_spreadsheet_formula_export(client):
    id=add(client,'formula.csv',b'Label,Amount\n=1+1,5\n@A1,-1\n')
    csv=client.get(f'/api/datasets/{id}/export/csv').text
    assert "'=1+1" in csv and "'@A1,-1" in csv
