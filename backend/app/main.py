import hashlib
import os
import random
import re
import secrets
import time
import threading
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated
from urllib.parse import quote

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, delete, update, func
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers
from starlette.middleware.gzip import GZipMiddleware

from .analytics import MAX_BYTES, Cleaning, analyze, chart, clean, fail, overview, pack, parse, records, safe_export, unpack
from .reports import build
from .store import Base, Dataset, Login, Report, Revision, Run, Session, User, engine, iso, now

COOKIE = 'pilot_session'
ph = PasswordHasher(memory_cost=32768, time_cost=3, parallelism=2)
DUMMY_HASH = ph.hash(secrets.token_hex(28))
rate = defaultdict(deque)
rate_lock = threading.Lock()


@asynccontextmanager
async def lifespan(_):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title='DataPilot AI API', version='1.0.0', lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1500)


class RequestLimit:
    def __init__(self, app): self.app = app
    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST','PUT','PATCH','DELETE'):
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        if headers.get('x-datapilot') != '1':
            return await JSONResponse({'detail':'Missing application request header.'},403)(scope,receive,send)
        max_body = MAX_BYTES + 65536 if scope['path'] == '/api/datasets' else 131072
        length = headers.get('content-length','0')
        if not length.isdecimal() or int(length) > max_body:
            return await JSONResponse({'detail':'Request is too large.'},413)(scope,receive,send)
        # Bound streamed bodies as well, including chunked multipart uploads.
        size = 0
        async def bounded():
            nonlocal size
            message = await receive()
            size += len(message.get('body', b''))
            if size > max_body: raise HTTPException(413,'Request is too large.')
            return message
        await self.app(scope,bounded,send)


app.add_middleware(RequestLimit)


@app.middleware('http')
async def safety_headers(request, call_next):
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    return response


def db_session():
    with Session() as db:
        yield db


DB = Annotated[object, Depends(db_session)]


def digest(token): return hashlib.sha256(token.encode()).hexdigest()


def current(request, db):
    token = request.cookies.get(COOKIE, '')
    if not token or len(token)>100: return None
    s = db.get(Login, digest(token))
    return db.get(User,s.user_id) if s and s.expires > now() else None


def protected(request: Request, db: DB):
    user = current(request, db)
    if not user: fail('Log in to access this workspace.',401)
    return user


Actor = Annotated[User, Depends(protected)]


def info(u): return {'id':u.id,'name':u.name,'email':None if u.demo else u.email,'demo':u.demo}


def secure(request):
    return os.getenv('COOKIE_SECURE','auto').lower() == 'true' or (os.getenv('COOKIE_SECURE','auto')=='auto' and (request.url.scheme=='https' or request.headers.get('x-forwarded-proto','').split(',')[0].strip()=='https' or request.headers.get('host','').split(':')[0].endswith('.e2b.app')))


def issue(db, request, response, user):
    old = request.cookies.get(COOKIE,'')
    if old and len(old)<100: db.execute(delete(Login).where(Login.id==digest(old)))
    token = secrets.token_urlsafe(32)
    lifetime = 1 if user.demo else 7
    db.add(Login(id=digest(token),user_id=user.id,expires=now()+timedelta(days=lifetime)))
    db.commit()
    tls = secure(request)
    response.set_cookie(COOKIE,token,max_age=86400*lifetime,httponly=True,samesite='none' if tls else 'lax',secure=tls,path='/')
    if tls: response.headers['set-cookie'] += '; Partitioned'
    return info(user)


class Credentials(BaseModel):
    email: str = Field(min_length=5,max_length=254)
    password: str = Field(min_length=1,max_length=128)


class Registration(Credentials):
    name: str = Field(min_length=1,max_length=80)


class Profile(BaseModel):
    name: str = Field(min_length=1,max_length=80)


def limit(request, action, n=12):
    if os.getenv('DISABLE_RATE_LIMIT')=='true': return
    ip = request.client.host if request.client else 'unknown'
    if os.getenv('TRUST_PROXY_IP') == 'true': ip = (request.headers.get('x-forwarded-for') or ip).split(',')[-1].strip()
    key = (ip,action)
    t = time.monotonic()
    with rate_lock:
        if len(rate)>4096:
            for k,v in list(rate.items()):
                if not v or v[-1]<t-300: rate.pop(k,None)
            if len(rate)>8192 and key not in rate: fail('Too many requests. Try again shortly.',429)
        q = rate[key]
        while q and q[0]<t-300: q.popleft()
        if len(q)>=n: fail('Too many attempts. Wait a few minutes and try again.',429)
        q.append(t)


@app.get('/api/health')
def health(db: DB):
    db.execute(select(1)); return {'status':'ok'}


@app.get('/api/auth/me')
def me(request: Request, db: DB):
    u = current(request,db)
    return {'user':info(u) if u else None}


@app.post('/api/auth/register',status_code=201)
def register(body: Registration, request: Request, response: Response, db: DB):
    limit(request,'auth')
    email, name = body.email.strip().lower(), body.name.strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email): fail('Enter a valid email address.')
    if len(body.password)<10: fail('Use a password with at least 10 characters.')
    if not name: fail('Enter your name.')
    u = User(name=name,email=email,password=ph.hash(body.password))
    db.add(u)
    try: db.flush()
    except IntegrityError: fail('An account with this email already exists.',409)
    return issue(db,request,response,u)


@app.post('/api/auth/login')
def login(body: Credentials, request: Request, response: Response, db: DB):
    limit(request,'auth')
    u = db.scalar(select(User).where(User.email==body.email.strip().lower(),User.demo.is_(False)))
    try: valid = ph.verify(u.password if u else DUMMY_HASH,body.password)
    except VerificationError: valid = False
    if not u or not valid: fail('Email or password is incorrect.',401)
    if ph.check_needs_rehash(u.password): u.password = ph.hash(body.password)
    return issue(db,request,response,u)


@app.post('/api/auth/logout')
def logout(request: Request, response: Response, db: DB):
    token = request.cookies.get(COOKIE,'')
    if token and len(token)<100:
        db.execute(delete(Login).where(Login.id==digest(token))); db.commit()
    tls = secure(request)
    response.delete_cookie(COOKIE,path='/',secure=tls,httponly=True,samesite='none' if tls else 'lax')
    if tls: response.headers['set-cookie'] += '; Partitioned'
    return {'ok':True}


@app.patch('/api/auth/profile')
def profile(body: Profile, db: DB, u: Actor):
    if not body.name.strip(): fail('Enter your name.')
    u.name = body.name.strip(); db.commit(); return info(u)


def quality(q): return {k:q[k] for k in ('rows','column_count','missing','duplicates','empty','memory','quality')}


def save_dataset(db, uid, name, raw, stamp=None):
    df = parse(name,raw)
    q, stats = overview(df), analyze(df)
    d = Dataset(user_id=uid,name=name,original=raw,data=pack(df),quality=quality(q),created=stamp or now())
    db.add(d); db.flush()
    db.add(Run(dataset_id=d.id,action='Dataset analyzed',findings=len(stats['insights']),created=d.created))
    return d,df,q,stats


def sample_data():
    rng = random.Random(41)
    names = [('Q4 Sales Performance.csv',240,['Date','Region','Revenue','Orders']),('Customer Retention.csv',185,['Date','Plan','Retention','Visits']),('Marketing Campaigns.csv',128,['Date','Channel','Spend','Conversions']),('Product Inventory.csv',96,['Date','Category','Stock','Reorders'])]
    for i,(name,n,cols) in enumerate(names):
        out = [','.join(cols)]
        for j in range(n):
            date = (now() - timedelta(days=n//4-(j//4))).date()
            cat = [['North','South','East','West'],['Standard','Plus','Enterprise'],['Search','Email','Social'],['Hardware','Accessories','Office']][i][j%([4,3,3,3][i])]
            value = round(190 + j*.66 + rng.gauss(0,37) + (j%4)*19, 2)
            values = [str(date),cat,str(value) if j%[27,13,79,20][i] else '',str(max(1,round(value/18+rng.gauss(0,1.3))))]
            out.append(','.join(values))
        if i in (0,1): out.append(out[-1])
        yield name, ('\n'.join(out)+'\n').encode()


@app.post('/api/auth/demo')
def demo(request: Request, response: Response, db: DB):
    existing = current(request,db)
    if existing: return info(existing)
    if os.getenv('ENABLE_DEMO','true') != 'true': fail('Sample workspaces are disabled.',403)
    limit(request,'demo',14)
    db.execute(delete(User).where(User.demo.is_(True),User.created<now()-timedelta(days=1)))
    u = User(name='Alex Morgan',email=f'{secrets.token_hex(16)}@demo.invalid',password='!',demo=True)
    db.add(u); db.flush()
    created = []
    for i,(name,raw) in enumerate(sample_data()):
        created.append(save_dataset(db,u.id,name,raw,now()-timedelta(days=[0,3,7,23][i])))
    for offset,n in [(1,2),(6,2),(9,2),(13,2),(17,1),(20,3),(26,1)]:
        for i in range(n):
            d,_,_,s = created[3 if offset>=8 else (2 if offset>=7 else (1 if offset>=3 else 0))]
            db.add(Run(dataset_id=d.id,action='Sample analysis',findings=len(s['insights']),created=now()-timedelta(days=offset,minutes=i*24)))
    for i in [0,2]:
        d,df,q,s=created[i]; make_report(db,u,d,df,q,s,created=d.created)
    return issue(db, request,response,u)


def dataset(db,u,id):
    d = db.scalar(select(Dataset).where(Dataset.id==id,Dataset.user_id==u.id))
    if not d: fail('Dataset not found.',404)
    return d


def report(db,u,id):
    r = db.scalar(select(Report).where(Report.id==id,Report.user_id==u.id))
    if not r: fail('Report not found.',404)
    return r


def dataset_body(d): return dict(id=d.id,name=d.name,created=iso(d.created),version=d.version,**d.quality)


def report_body(r): return dict(id=r.id,name=r.name,dataset_id=r.dataset_id,created=iso(r.created),**r.summary)


def runs(db,u,limit=500):
    rows = db.execute(select(Run, Dataset.name).join(Dataset,Dataset.id==Run.dataset_id).where(Dataset.user_id==u.id).order_by(Run.created.desc(),Run.id.desc()).limit(limit))
    return [dict(id=r.id,dataset_id=r.dataset_id,name=name,created=iso(r.created),action=r.action,findings=r.findings) for r,name in rows]


@app.get('/api/dashboard')
def dashboard(db: DB,u: Actor):
    data = list(db.scalars(select(Dataset).where(Dataset.user_id==u.id).order_by(Dataset.created.desc())))
    reports = db.scalar(select(func.count()).select_from(Report).where(Report.user_id==u.id))
    all_findings = db.scalar(select(func.coalesce(func.sum(Run.findings),0)).join(Dataset).where(Dataset.user_id==u.id))
    stamp = now().replace(hour=0,minute=0,second=0,microsecond=0)
    activity = list(db.scalars(select(Run.created).join(Dataset).where(Dataset.user_id==u.id,Run.created>=stamp-timedelta(days=29))))
    daily = { (stamp-timedelta(days=i)).date().isoformat(): 0 for i in reversed(range(30)) }
    for t in activity:
        key = t.date().isoformat()
        if key in daily: daily[key] += 1
    cells = sum(d.quality['rows']*d.quality['column_count'] for d in data)
    missing = sum(d.quality['missing'] for d in data)
    return dict(datasets=len(data),reports=reports,findings=all_findings,quality=round(100*(1-missing/cells),1) if cells else None,missing=missing,cells=cells,duplicates=sum(d.quality['duplicates'] for d in data),recent=[dataset_body(d) for d in data[:5]],activity=[dict(date=k,value=v) for k,v in daily.items()],recent_runs=runs(db,u,5))


@app.get('/api/datasets')
def datasets(db: DB,u: Actor):
    return [dataset_body(d) for d in db.scalars(select(Dataset).where(Dataset.user_id==u.id).order_by(Dataset.created.desc()))]


@app.post('/api/datasets',status_code=201)
def upload(db: DB,u: Actor,file: UploadFile=File(...)):
    if db.scalar(select(func.count()).select_from(Dataset).where(Dataset.user_id==u.id))>=50: fail('This workspace holds up to 50 datasets.',409)
    name = re.split(r'[/\\]',file.filename or '')[-1].strip()
    name = ''.join(c for c in name if ord(c)>=32 and ord(c)!=127)
    if not name or len(name)>180: fail('Use a file name between 1 and 180 characters.')
    try: raw=file.file.read(MAX_BYTES+1)
    finally: file.file.close()
    d,_,_,_=save_dataset(db,u.id,name,raw); db.commit(); return dataset_body(d)


@app.get('/api/datasets/{id}')
def details(id: int,db: DB,u: Actor):
    d=dataset(db,u,id); df=unpack(d.data)
    revisions = list(db.scalars(select(Revision).where(Revision.dataset_id==id).order_by(Revision.id.desc()).limit(30)))
    return {**dataset_body(d),**overview(df),**analyze(df),'revisions':[dict(r.details,id=r.id,undone=r.undone) for r in revisions],'can_undo':any(not r.undone and r.previous is not None for r in revisions)}


@app.delete('/api/datasets/{id}')
def remove_dataset(id:int,db:DB,u:Actor):
    d=dataset(db,u,id); db.delete(d); db.commit(); return {'ok':True}


@app.get('/api/datasets/{id}/records')
def search_records(id:int,db:DB,u:Actor,q:str=Query('',max_length=150),column:str=Query('',max_length=120),page:int=Query(1,ge=1),operator:str=Query('contains',pattern='^(contains|equals|missing)$')):
    df=unpack(dataset(db,u,id).data)
    if column and column not in df: fail('Column not found.')
    if q or operator=='missing':
        mask = None
        for c in [column] if column else df.columns:
            s = df[c]
            matches = s.isna() if operator=='missing' else (s.astype('string').str.casefold().eq(q.casefold()).fillna(False) if operator=='equals' else s.astype('string').str.contains(q,case=False,regex=False,na=False))
            mask = matches if mask is None else mask | matches
        df=df[mask]
    start=(page-1)*15
    return {'rows':records(df.iloc[start:start+15]),'total':len(df),'page':page}


@app.post('/api/datasets/{id}/analyze')
def rerun(id:int,db:DB,u:Actor):
    d=dataset(db,u,id); stats=analyze(unpack(d.data)); db.add(Run(dataset_id=id,action='Analysis rerun',findings=len(stats['insights']))); db.commit(); return stats


@app.post('/api/datasets/{id}/clean/preview')
def preview_clean(id:int,body:Cleaning,db:DB,u:Actor):
    d=dataset(db,u,id)
    if d.version!=body.version: fail('This dataset changed. Refresh and preview again.',409)
    return overview(clean(unpack(d.data),body))


def change(db,d,new,action,previous,operations=()):
    q=quality(overview(new))
    payload=dict(action=action,before=d.quality,after=q,date=iso(now()),operations=list(operations))
    updated=db.execute(update(Dataset).where(Dataset.id==d.id,Dataset.version==d.version).values(version=d.version+1,data=pack(new),quality=q))
    if updated.rowcount!=1: fail('This dataset changed. Refresh and try again.',409)
    db.add(Revision(dataset_id=d.id,previous=previous,details=payload))
    db.add(Run(dataset_id=d.id,action=action,findings=len(analyze(new)['insights'])))
    db.commit(); return payload


@app.post('/api/datasets/{id}/clean/apply')
def apply_clean(id:int,body:Cleaning,db:DB,u:Actor):
    d=dataset(db,u,id)
    if d.version!=body.version: fail('This dataset changed. Refresh and try again.',409)
    if db.scalar(select(func.count()).select_from(Revision).where(Revision.dataset_id==id))>=30: fail('This dataset has reached its 30-revision limit. Export and re-upload to start a new history.',409)
    operations = ([] if not body.empty else ['Remove empty rows']) + ([] if not body.duplicates else ['Remove duplicate rows'])
    for r in body.rules:
        steps = ['remove column'] if r.remove else ([f'convert to {r.cast}'] if r.cast!='keep' else []) + ([f'missing values: {r.method}'] if r.method!='keep' else []) + ([f'rename to {r.rename}'] if r.rename.strip() else [])
        if steps: operations.append(f"{r.column}: {', '.join(steps)}")
    if not operations: fail('Select at least one cleaning operation.')
    return change(db,d,clean(unpack(d.data),body),'Cleaning applied',d.data,operations)


@app.post('/api/datasets/{id}/clean/undo')
def undo_clean(id:int,db:DB,u:Actor):
    d=dataset(db,u,id)
    revision = db.scalar(select(Revision).where(Revision.dataset_id==id,Revision.undone.is_(False),Revision.previous.is_not(None)).order_by(Revision.id.desc()).limit(1))
    if not revision: fail('There is no cleaning step to undo.',409)
    revision.undone=True
    return change(db,d,unpack(revision.previous),'Cleaning undone',None)


@app.post('/api/datasets/{id}/clean/reset')
def reset_clean(id:int,db:DB,u:Actor):
    d=dataset(db,u,id)
    if db.scalar(select(func.count()).select_from(Revision).where(Revision.dataset_id==id))>=30: fail('Undo previous cleaning steps to return to the original.',409)
    return change(db,d,parse(d.name,d.original),'Reset to original',d.data)


@app.get('/api/datasets/{id}/chart')
def chart_data(id:int,db:DB,u:Actor,type:str='bar',x:str='',y:str='',aggregate:str='mean'):
    return chart(unpack(dataset(db,u,id).data),type,x,y,aggregate)


def attachment(payload,name,content_type,inline=False):
    filename=quote(name,safe='')
    fallback = re.sub(r'[^a-zA-Z0-9. _-]', '-', name)
    disposition = 'inline' if inline else 'attachment'
    return Response(payload,media_type=content_type,headers={'Content-Disposition':f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{filename}"})


@app.get('/api/datasets/{id}/export/{ext}')
def export_dataset(id:int,ext:str,db:DB,u:Actor):
    if ext not in ('csv','xlsx'): fail('Choose CSV or XLSX.')
    d=dataset(db,u,id)
    mime='text/csv' if ext=='csv' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return attachment(safe_export(unpack(d.data),ext), d.name.rsplit('.',1)[0]+'-processed.'+ext,mime)


@app.get('/api/history')
def history(db:DB,u:Actor): return runs(db,u)


def make_report(db,u,d,df,q,stats,created=None):
    rid=secrets.token_hex(12); created=created or now()
    revs=list(db.scalars(select(Revision).where(Revision.dataset_id==d.id,Revision.undone.is_(False)).order_by(Revision.id)))
    active=[]
    for rev in revs:
        if rev.details['action']=='Reset to original': active=[rev.details]
        elif rev.details['action']=='Cleaning applied': active.append(rev.details)
    pdf,docx=build(rid,d.name,created.strftime('%Y-%m-%d %H:%M'),df,q,stats,active)
    r=Report(id=rid,user_id=u.id,dataset_id=d.id,name=d.name.rsplit('.',1)[0]+' — Analysis Report',created=created,pdf=pdf,docx=docx,summary={'dataset_name':d.name,'rows':q['rows'],'columns':q['column_count'],'quality':q['quality'],'findings':sum(i['category']=='Finding' for i in stats['insights']),'dataset_version':d.version,'preview':[i for i in stats['insights'] if i['category']=='Finding'][:4]})
    db.add(r); return r


@app.get('/api/reports')
def reports(db:DB,u:Actor):
    return [report_body(r) for r in db.scalars(select(Report).where(Report.user_id==u.id).order_by(Report.created.desc()))]


@app.post('/api/datasets/{id}/reports',status_code=201)
def generate(id:int,db:DB,u:Actor,request:Request):
    limit(request,'report',25)
    if db.scalar(select(func.count()).select_from(Report).where(Report.user_id==u.id))>=60: fail('This workspace holds up to 60 reports.',409)
    d=dataset(db,u,id); df=unpack(d.data)
    r=make_report(db,u,d,df,overview(df),analyze(df)); db.commit(); return report_body(r)


@app.get('/api/reports/{id}')
def report_details(id:str,db:DB,u:Actor): return report_body(report(db,u,id))


@app.get('/api/reports/{id}/file/{ext}')
def report_download(id:str,ext:str,db:DB,u:Actor,inline:bool=False):
    r=report(db,u,id)
    if ext=='pdf': return attachment(r.pdf,r.name+'.pdf','application/pdf',inline)
    if ext=='docx': return attachment(r.docx,r.name+'.docx','application/vnd.openxmlformats-officedocument.wordprocessingml.document')
    fail('Choose PDF or DOCX.')


@app.delete('/api/reports/{id}')
def report_delete(id:str,db:DB,u:Actor):
    db.delete(report(db,u,id)); db.commit(); return {'ok':True}


# In production the compiled React bundle shares the API origin. During
# development Vite serves the bundle and proxies /api to this application.
if os.getenv('STATIC_DIR'):
    from pathlib import Path
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    static = Path(os.environ['STATIC_DIR'])
    app.mount('/assets', StaticFiles(directory=static/'assets'), name='web-assets')

    @app.get('/',include_in_schema=False)
    def frontend():
        return FileResponse(static/'index.html',headers={'Content-Security-Policy':"default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",'Referrer-Policy':'no-referrer'})

    @app.get('/mark.svg',include_in_schema=False)
    def mark(): return FileResponse(static/'mark.svg', media_type='image/svg+xml')
