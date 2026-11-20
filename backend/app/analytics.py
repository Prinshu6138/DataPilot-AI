import csv
import io
import json
import re
import zipfile
from pathlib import Path
import numpy as np
import pandas as pd
from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 50000
MAX_COLS = 80
ISO_DATE = r'^\d{4}-\d\d?-\d\d?(?:[T ].*)?$'


def fail(message, status=422):
    raise HTTPException(status, message)


def parse(name, data):
    ext = Path(name).suffix.lower()
    if ext not in {'.csv', '.xlsx', '.xls'}:
        fail('Choose a CSV, XLS, or XLSX file.', 415)
    if not data:
        fail('The file is empty.')
    if len(data) > MAX_BYTES:
        fail('The maximum upload size is 8 MB.', 413)
    try:
        if ext == '.csv':
            text = data.decode('utf-8-sig')
            if '\x00' in text or any(ord(c) < 32 and c not in '\r\n\t' for c in text):
                fail('This is not a plain-text CSV file.')
            try:
                dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|')
            except csv.Error:
                dialect = csv.excel
            reader = csv.reader(io.StringIO(text, newline=''), dialect, strict=True)
            header = next(reader)
            if not header or len(header) > MAX_COLS:
                fail('Datasets must have between 1 and 80 columns.')
            for i, row in enumerate(reader, 1):
                if len(row) > len(header):
                    fail(f'CSV record {i + 1} has more fields than the header.')
                if i > MAX_ROWS:
                    fail('Datasets can contain at most 50,000 rows.')
            df = pd.read_csv(io.StringIO(text), sep=dialect.delimiter, quotechar=dialect.quotechar or '"', skip_blank_lines=False, nrows=MAX_ROWS + 1)
        else:
            if ext == '.xlsx':
                with zipfile.ZipFile(io.BytesIO(data)) as archive:
                    members = archive.infolist()
                    if len(members) > 1000 or sum(i.file_size for i in members) > 48 * 1024 * 1024:
                        fail('The expanded Excel workbook is too large.', 413)
                    if 'xl/workbook.xml' not in archive.namelist():
                        fail('This is not a valid XLSX workbook.')
            elif not data.startswith(bytes.fromhex('D0CF11E0A1B11AE1')):
                fail('This is not a valid XLS workbook.')
            df = pd.read_excel(io.BytesIO(data), engine='openpyxl' if ext == '.xlsx' else 'xlrd', nrows=MAX_ROWS + 1)
    except HTTPException:
        raise
    except Exception:
        fail('This file could not be read. Check its format and try again.')
    if len(df) > MAX_ROWS or not 0 < len(df.columns) <= MAX_COLS:
        fail('Datasets must have at most 50,000 rows and between 1 and 80 columns.')
    if df.empty:
        fail('Add at least one data row beneath the column headers.')
    if any(len(str(c)) > 120 for c in df.columns):
        fail('Column names must be at most 120 characters.')
    df = df.replace([np.inf, -np.inf], np.nan)
    df.columns = df.columns.map(str)
    for c in df:
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            df[c] = pd.to_datetime(s, utc=True)
        elif not pd.api.types.is_numeric_dtype(s):
            s = s.astype('string').str.strip().replace('', pd.NA)
            if s.notna().any() and s.dropna().str.match(ISO_DATE).all():
                dates = pd.to_datetime(s, errors='coerce', utc=True, format='mixed')
                if dates.notna().sum() == s.notna().sum():
                    df[c] = dates
                    continue
            df[c] = s
    return df


def kind(series):
    if pd.api.types.is_datetime64_any_dtype(series): return 'Date'
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series): return 'Numeric'
    return 'Categorical'


def records(df):
    return json.loads(df.to_json(orient='values', date_format='iso', double_precision=12))


def overview(df):
    missing = int(df.isna().sum().sum())
    columns = [{'name': c, 'type': kind(df[c]), 'dtype': str(df[c].dtype), 'missing': int(df[c].isna().sum()), 'unique': int(df[c].nunique())} for c in df]
    return dict(rows=len(df), column_count=len(columns), columns=columns, missing=missing, duplicates=int(df.duplicated().sum()), empty=int(df.isna().all(axis=1).sum()), memory=int(df.memory_usage(deep=True).sum()), quality=round(100 * (1 - missing / max(1, df.size)), 1), preview=records(df.head(15)))


class Rule(BaseModel):
    column: str = Field(max_length=120)
    rename: str = Field(default='', max_length=120)
    method: str = Field(default='keep', pattern='^(keep|mean|median|mode|drop_rows|unknown)$')
    cast: str = Field(default='keep', pattern='^(keep|number|text|date)$')
    remove: bool = False


class Cleaning(BaseModel):
    duplicates: bool = False
    empty: bool = False
    rules: list[Rule] = Field(default_factory=list, max_length=80)
    version: int = Field(default=0, ge=0)


def clean(df, config):
    df = df.copy()
    if config.empty: df = df.dropna(how='all')
    names = set()
    renames = {}
    for rule in config.rules:
        c = rule.column
        if c not in df or c in names: fail('Cleaning rules must refer to distinct, existing columns.')
        names.add(c)
        if rule.remove:
            df = df.drop(columns=c)
            continue
        if rule.cast == 'number': df[c] = pd.to_numeric(df[c], errors='coerce').replace([np.inf, -np.inf], np.nan)
        if rule.cast == 'date': df[c] = pd.to_datetime(df[c], errors='coerce', utc=True, format='mixed')
        if rule.cast == 'text': df[c] = df[c].astype('string')
        if rule.method in {'mean', 'median'}:
            if kind(df[c]) != 'Numeric': fail(f'Use numeric conversion first for {c}.')
            if df[c].notna().any():
                v = getattr(df[c], rule.method)()
                df[c] = df[c].astype(float).fillna(v)
        if rule.method == 'mode':
            m = df[c].mode(dropna=True)
            if not m.empty: df[c] = df[c].fillna(m.iloc[0])
        if rule.method == 'unknown':
            if kind(df[c]) != 'Categorical': fail(f'“Unknown” is only suitable for categorical data ({c}).')
            df[c] = df[c].astype('string').fillna('Unknown')
        if rule.method == 'drop_rows': df = df.dropna(subset=[c])
        if rule.rename.strip(): renames[c] = rule.rename.strip()
    df = df.rename(columns=renames)
    if not df.shape[1] or not df.columns.is_unique: fail('Keep at least one column and use unique column names.')
    if config.duplicates: df = df.drop_duplicates()
    return df.reset_index(drop=True)


def number(v):
    if pd.isna(v) or not np.isfinite(v): return None
    return float(v)


def analyze(df):
    o = overview(df)
    nums = [c for c in df if kind(df[c]) == 'Numeric']
    cats = [c for c in df if kind(df[c]) == 'Categorical']
    dates = [c for c in df if kind(df[c]) == 'Date']
    numeric, categorical, insights = [], [], []
    def add(title, body, category='Finding'):
        insights.append(dict(title=title, body=body, category=category))
    if o['missing']:
        most = max(o['columns'], key=lambda x: x['missing'])
        add('Missing-data pattern', f"{o['missing']:,} cells are missing across {sum(c['missing'] > 0 for c in o['columns'])} columns. {most['name']} has the most missing values ({most['missing']:,}).")
        add('Review missing records', f"Before imputing values in {most['name']}, check whether blanks cluster by category or time period.", 'Recommendation')
    if o['duplicates']:
        add('Duplicate records', f"{o['duplicates']:,} rows exactly repeat an earlier row.")
    if not o['missing'] and not o['duplicates'] and len(df):
        add('No missing cells or duplicates', f'All {df.size:,} cells are populated and no exact duplicate rows were found.')
    for c in nums:
        s = df[c].dropna().astype(float)
        m = s.mode()
        mode_count = int((s == m.iloc[0]).sum()) if not m.empty else 0
        if len(s):
            q1, q3 = s.quantile([.25, .75]); spread = q3 - q1
            n = int(((s < q1 - 1.5 * spread) | (s > q3 + 1.5 * spread)).sum()) if len(s) >= 4 else 0
        else: q1, q3, n = np.nan, np.nan, 0
        numeric.append(dict(column=c, count=len(s), mean=number(s.mean()), median=number(s.median()), mode=number(m.iloc[0]) if mode_count > 1 else None, min=number(s.min()), max=number(s.max()), std=number(s.std()), variance=number(s.var()), q1=number(q1), q3=number(q3), outliers=n))
        if len(numeric) <= 6 and len(s):
            add(f'{c} range and variation', f'{c} ranges from {s.min():,.5g} to {s.max():,.5g} across {len(s):,} observations. The median is {s.median():,.5g}.' + (f' Sample standard deviation: {s.std():,.5g}.' if np.isfinite(s.std()) else ''))
            if n:
                add(f'Unusual observations in {c}', f'{n:,} values fall outside the 1.5 × interquartile range fences. These are potential outliers, not confirmed errors.')
    for c in cats:
        v = df[c].dropna().astype(str).value_counts()
        categorical.append(dict(column=c, categories=len(v), frequencies=[dict(name=str(k), value=int(n)) for k,n in v.head(20).items()]))
    if cats and nums:
        eligible = next((c for c in cats if 2 <= df[c].nunique() <= 30), None)
        if eligible:
            for metric in nums[:2]:
                means = df.dropna(subset=[metric]).groupby(eligible, dropna=True)[metric].agg(['mean','count']).sort_values('mean')
                if len(means) >= 2 and means.iloc[-1]['mean'] != means.iloc[0]['mean']:
                    add(f'{metric} by {eligible}', f"{means.index[-1]} has the highest average {metric} ({means.iloc[-1]['mean']:,.5g}, n={int(means.iloc[-1]['count'])}); {means.index[0]} has the lowest ({means.iloc[0]['mean']:,.5g}, n={int(means.iloc[0]['count'])}).")
    if dates and nums:
        for metric in nums[:2]:
            date = dates[0]
            valid = df[[date,metric]].dropna().sort_values(date)
            if len(valid) > 1:
                granularity = 'D' if (valid[date].max()-valid[date].min()).days < 31 else 'M'
                series = valid.groupby(valid[date].dt.tz_localize(None).dt.to_period(granularity))[metric].mean()
                if len(series) > 1:
                    a, b = series.iloc[0], series.iloc[-1]
                    delta = b-a
                    add(f'{metric} over time', f'Average {metric} moved from {a:,.5g} in {series.index[0]} to {b:,.5g} in {series.index[-1]} ({delta:+,.5g} absolute)' + (f', a relative change of {delta/abs(a):+.1%}.' if a else '.') + ' This compares the first and last observed periods; it is not a forecast.')
    corr = df[nums].corr(min_periods=3) if nums else pd.DataFrame()
    pairs = sorted([(abs(r), a,b,r) for i,a in enumerate(nums) for b in nums[:i] if (r:=corr.loc[a,b]) == r and abs(r) >= .7],reverse=True)
    for _,a,b,r in pairs[:5]:
        n = int(df[[a,b]].dropna().shape[0])
        add('Strong ' + ('positive' if r>0 else 'negative') + ' correlation', f'{a} and {b} have a Pearson correlation of {r:.2f} across {n:,} paired rows. Correlation does not establish causation.')
    if pairs: add('Validate related metrics', 'Check whether shared inputs, time effects, or segment differences explain the strongest correlations.', 'Recommendation')
    if not nums and cats:
        for item in categorical[:4]:
            top = item['frequencies'][:1]
            if top: add(f"Most common {item['column']}", f"{top[0]['name']} appears {top[0]['value']:,} times across {item['categories']:,} distinct non-missing values.")
    return dict(numeric=numeric, categorical=categorical, correlation={'columns':nums, 'values':records(corr)}, insights=insights)


def chart(df, chart_type, x='', y='', aggregate='mean'):
    if chart_type == 'heatmap':
        cols = [c for c in df if kind(df[c]) == 'Numeric']
        return {'columns': cols, 'values': records(df[cols].corr(min_periods=3))}
    if x not in df or (y and y not in df): fail('Choose valid chart columns.')
    if aggregate not in ('mean','sum','count','median'): fail('Unknown aggregation.')
    if chart_type == 'histogram':
        if kind(df[x]) != 'Numeric': fail('A histogram requires a numeric column.')
        v = df[x].dropna().to_numpy(dtype=float)
        if not len(v): return {'points':[]}
        counts, edges = np.histogram(v,bins=10)
        return {'points':[{'name':f'{edges[i]:.4g} – {edges[i+1]:.4g}', 'value':int(n)} for i,n in enumerate(counts)]}
    if chart_type == 'scatter':
        if not y or kind(df[x]) != 'Numeric' or kind(df[y]) != 'Numeric': fail('A scatter plot needs two numeric columns.')
        data = df[[x,y]].dropna()
        if len(data)>750: data = data.sample(750,random_state=17)
        return {'points':[{'x':a,'y':b} for a,b in records(data)], 'sampled': len(df[[x,y]].dropna())>750}
    if chart_type not in ('bar','line','pie'): fail('Unknown chart type.')
    if y and kind(df[y]) != 'Numeric' and aggregate != 'count': fail('Choose a numeric measure or use row count.')
    labels = df[x]
    if chart_type == 'line' and kind(labels) == 'Date':
        if labels.nunique() > 45: labels = labels.dt.strftime('%Y-%m')
        else: labels = labels.dt.strftime('%Y-%m-%d')
    group = pd.DataFrame({'key':labels, 'measure': df[y] if y else 1}).groupby('key', dropna=True)
    values = group.size() if not y or aggregate == 'count' else group['measure'].agg(aggregate).dropna()
    if chart_type in ('pie','bar'): values = values.sort_values(ascending=False)
    if chart_type == 'pie' and (values.lt(0).any() or not values.gt(0).any()): fail('Pie charts require non-negative values and a positive total.')
    limit = {'pie':8,'bar':20,'line':60}[chart_type]
    points = [{'name':str(k), 'value':number(v)} for k,v in values.head(limit).items() if number(v) is not None]
    if chart_type == 'pie' and len(values)>limit:
        points.append({'name':'Other groups', 'value': number(values.iloc[limit:].sum())})
    return {'points':points,'truncated': len(values)>limit and chart_type != 'pie'}


def pack(df):
    import zlib
    return zlib.compress(json.dumps({'columns':list(df.columns), 'types':[str(t) for t in df.dtypes], 'rows':json.loads(df.to_json(orient='values', date_format='iso', date_unit='ns', double_precision=15))}, ensure_ascii=False).encode(), 3)


def unpack(data):
    import zlib
    obj = json.loads(zlib.decompress(data))
    df = pd.DataFrame(obj['rows'], columns=obj['columns'])
    for c, t in zip(obj['columns'], obj['types']):
        if t.startswith('datetime'): df[c] = pd.to_datetime(df[c], errors='coerce', utc=True, format='mixed')
        elif t in ('object','string'): df[c] = df[c].astype('string')
        else: df[c] = df[c].astype(t)
    return df


def safe_export(df, ext):
    result = df.copy()
    def protect(v):
        return "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@')) else v
    result.columns = [protect(c) for c in result.columns]
    for c in result:
        if kind(result[c]) == 'Date': result[c] = result[c].dt.tz_convert(None)
        elif kind(result[c]) == 'Categorical': result[c] = result[c].map(protect)
    if ext == 'csv': return result.to_csv(index=False).encode('utf-8-sig')
    buf = io.BytesIO(); result.to_excel(buf, index=False, engine='openpyxl'); return buf.getvalue()
