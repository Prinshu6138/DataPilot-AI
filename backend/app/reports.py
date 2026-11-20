import io
import re
import threading
from xml.sax.saxutils import escape
from docx import Document
from docx.shared import Inches
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
from .analytics import chart

LOCK = threading.Lock()


def display(value):
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]', ' ', str(value))
    return text if len(text) <= 1800 else text[:1799]+'…'



def plot(df, overview, stats):
    if not len(df): return None, ''
    nums = [n['column'] for n in stats['numeric'] if n['count']]
    cats = [c['column'] for c in stats['categorical'] if c['categories']]
    dates = [c['name'] for c in overview['columns'] if c['type']=='Date' and c['unique']]
    if dates and nums:
        typ, title, x, y = 'line', f'Average {nums[0]} by {dates[0]}', dates[0], nums[0]
    elif cats:
        typ, title, x, y = 'bar', f'Record count by {cats[0]}', cats[0], ''
    elif nums:
        typ, title, x, y = 'histogram', f'Distribution of {nums[0]}', nums[0], ''
    elif dates:
        typ, title, x, y = 'line', f'Record count by {dates[0]}', dates[0], ''
    else: return None, ''
    points = chart(df, typ, x, y)['points'][:16]
    if not points: return None, ''
    with LOCK:
        fig = Figure(figsize=(7,2.6), dpi=144, layout='constrained'); ax = fig.add_subplot()
        xs = range(len(points)); v = [p['value'] for p in points]
        ax.grid(axis='y', color='#e9e4f5'); ax.set_axisbelow(True)
        if typ == 'line': ax.plot(xs, v, color='#7047ec', linewidth=2, marker='o', markersize=3)
        else: ax.bar(xs,v,color='#7047ec',alpha=.85)
        ax.set_xticks(list(xs),[p['name'][:22] for p in points],rotation=35, ha='right',fontsize=7)
        ax.spines[['top','right','left']].set_visible(False); ax.spines['bottom'].set_color('#ded9ea'); ax.tick_params(length=0,labelsize=8)
        buf = io.BytesIO(); FigureCanvasAgg(fig).print_png(buf)
    return buf.getvalue(), title


def build(report_id, name, stamp, df, q, stats, revisions):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from pathlib import Path
    from matplotlib import get_data_path
    if 'Pilot' not in pdfmetrics.getRegisteredFontNames():
        fonts = Path(get_data_path())/'fonts'/'ttf'
        with LOCK:
            pdfmetrics.registerFont(TTFont('Pilot', str(fonts/'DejaVuSans.ttf')))
            pdfmetrics.registerFont(TTFont('PilotBold', str(fonts/'DejaVuSans-Bold.ttf')))
    pbuf, dbuf = io.BytesIO(), io.BytesIO()
    doc = Document(); doc.core_properties.title = 'DataPilot AI Analysis Report'
    normal = ParagraphStyle('PilotBody',fontName='Pilot',fontSize=9,leading=14,textColor=colors.HexColor('#39334e'),spaceAfter=7)
    heading = ParagraphStyle('PilotHeading',parent=normal,fontName='PilotBold',fontSize=13,leading=18,spaceBefore=13,spaceAfter=6,textColor=colors.HexColor('#251b47'))
    title = ParagraphStyle('PilotTitle',parent=heading,fontSize=26,leading=32,textColor=colors.HexColor('#6c42e6'))
    story = [Paragraph('DataPilot AI', title),Paragraph('Intelligent Data Analysis Report',heading),Paragraph(escape(f'Report {report_id[:12].upper()}  •  {stamp} UTC'),normal),Spacer(1,9)]
    doc.add_heading('DataPilot AI',0); doc.add_heading('Intelligent Data Analysis Report',1); doc.add_paragraph(f'Report {report_id[:12].upper()}  •  {stamp} UTC')
    def section(name, text=None):
        story.append(Paragraph(escape(name),heading)); doc.add_heading(name,2)
        if text: paragraph(text)
    def paragraph(text):
        story.append(Paragraph(escape(display(text)),normal)); doc.add_paragraph(display(text))
    def grid(headers, rows):
        small = ParagraphStyle('PilotCell',parent=normal,fontSize=8,leading=11,spaceAfter=0,splitLongWords=True)
        matrix = [[Paragraph(escape(display(x)),small) for x in row] for row in [headers,*rows]]
        t = Table(matrix,colWidths=[500/len(headers)]*len(headers),repeatRows=1,hAlign='LEFT'); t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#ece5ff')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#f8f6fc')]),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)])); story.append(t)
        dt = doc.add_table(rows=1,cols=len(headers)); dt.style = 'Light Shading Accent 1'
        for c,v in zip(dt.rows[0].cells,headers): c.text=display(v)
        for row in rows:
            for c,v in zip(dt.add_row().cells,row): c.text=display(v)
    section('01 / Dataset information',name)
    section('02 / Dataset overview',f"{q['rows']:,} rows across {q['column_count']} columns. Estimated in-memory size: {q['memory']:,} bytes.")
    grid(['Column','Type','Unique','Missing'],[[c['name'],c['type'],c['unique'],c['missing']] for c in q['columns']])
    section('03 / Data quality',f"Completeness: {q['quality']}%. Missing cells: {q['missing']:,}. Exact duplicates: {q['duplicates']:,}. Empty rows: {q['empty']:,}.")
    section('04 / Cleaning summary')
    if not revisions: paragraph('No active cleaning revisions.')
    for r in revisions[-12:]:
        paragraph(f"{r['date']} — {r['action']}: {r['before']['rows']:,} → {r['after']['rows']:,} rows, {r['before']['missing']:,} → {r['after']['missing']:,} missing cells.")
        if r.get('operations'): paragraph(', '.join(r['operations']))
    section('05 / Statistical analysis')
    def fmt(x): return '—' if x is None else f'{x:,.5g}'
    for n in stats['numeric']:
        paragraph(n['column'])
        grid(['Count','Mean','Median','Min','Max','Std. dev.'],[[n['count'], *[fmt(n[k]) for k in ['mean','median','min','max','std']]]])
        grid(['Mode','Variance','Q1','Q3','Potential outliers'],[[*[fmt(n[k]) for k in ['mode','variance','q1','q3']],n['outliers']]])
    for c in stats['categorical']:
        common = ', '.join(f"{v['name']} ({v['value']:,})" for v in c['frequencies'][:5])
        paragraph(f"{c['column']}: {c['categories']:,} distinct categories." + (f' Most frequent: {common}.' if common else ''))
    if not (stats['categorical'] or stats['numeric']): paragraph('No numeric or categorical columns are available.')
    section('06 / Data visualizations')
    image, caption = plot(df, q, stats)
    if image:
        paragraph(caption); story.append(Image(io.BytesIO(image),width=490,height=182)); doc.add_picture(io.BytesIO(image),width=Inches(6.6))
    else: paragraph('No suitable chart is available for this dataset.')
    findings = [i for i in stats['insights'] if i['category']=='Finding']; recs = [i for i in stats['insights'] if i['category']=='Recommendation']
    section('07 / Intelligent insights','These descriptive insights are computed from the dataset. They are not predictions.')
    for i in findings: paragraph(f"{i['title']}: {i['body']}")
    if not findings: paragraph('No findings met the descriptive rules.')
    section('08 / Important observations',f"The analysis used {q['rows']:,} current records and identified {len(findings)} descriptive findings. Missing values are excluded from calculations where appropriate.")
    section('09 / Recommendations')
    for i in recs: paragraph(i['body'])
    if not recs: paragraph('Confirm that the current records and date ranges are representative before making decisions.')
    section('10 / Conclusion','Use this report as a snapshot of the current dataset. Review provenance, limitations, and the statistical assumptions before drawing business conclusions.')
    def footer(canvas, doc):
        canvas.setFont('Pilot',8); canvas.setFillColor(colors.HexColor('#69617d')); canvas.drawString(48,28,'DATAPILOT AI  /  ANALYSIS REPORT'); canvas.drawRightString(547,28,f'Page {doc.page}')
    SimpleDocTemplate(pbuf,pagesize=A4,topMargin=38,bottomMargin=48,leftMargin=47,rightMargin=47,title='DataPilot AI Analysis Report').build(story,onFirstPage=footer,onLaterPages=footer)
    doc.save(dbuf)
    return pbuf.getvalue(), dbuf.getvalue()
