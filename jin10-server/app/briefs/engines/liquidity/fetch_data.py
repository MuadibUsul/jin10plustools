"""Free ChinaMoney source used by the normal pledged-repo quotation page."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CHINA=timezone(timedelta(hours=8))
PAGE='https://www.chinamoney.com.cn/chinese/mkdatapm/?tab=2'
BASE='https://www.chinamoney.com.cn/r/cms/www/chinamoney/data/currency/'
HISTORY_COLUMNS={'DR001':6,'DR007':7,'DR014':8}
MAX_BYTES=8*1024*1024


class SourceError(RuntimeError):
    def __init__(self,code,message):
        super().__init__(message);self.code=code


def numeric(text,field,*,allow_missing=True):
    if text is None or (isinstance(text,str) and text.strip() in ('','---','--','—','-')):
        if allow_missing:return None
        raise SourceError('missing_value',f'Missing {field}')
    if isinstance(text,bool) or '%' in str(text):
        raise SourceError('invalid_value',f'{field} must be a bare number')
    try:value=Decimal(str(text))
    except InvalidOperation:raise SourceError('invalid_value',f'Invalid {field}') from None
    lower,upper=(Decimal(0),Decimal(400)) if field=='average term' else (Decimal(-10),Decimal(100))
    if not value.is_finite() or not lower<=value<=upper:
        raise SourceError('invalid_value',f'{field} is not a finite plausible percentage/term')
    return str(text)


def parse_quotes(raw,expected_date):
    try:
        payload=json.loads(raw.decode('utf-8-sig'),parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if str(payload['head']['rep_code'])!='200':raise ValueError()
        stamp=payload['data']['showDateCN']
        updated=datetime.strptime(stamp,'%Y-%m-%d %H:%M').replace(tzinfo=CHINA)
        records=payload['records']
        if not isinstance(records,list):raise ValueError()
    except (ValueError,KeyError,TypeError,UnicodeError):
        raise SourceError('source_changed','Quotation response does not match the official page schema') from None
    if updated.date().isoformat()!=expected_date:
        raise SourceError('pending_dr007',f'Quotation data day {updated.date()} does not match required day {expected_date}')
    rows=[];seen=set()
    for record in records:
        code=record.get('productCode','')
        if not re.fullmatch(r'DR\d{3}',code):
            raise SourceError('source_changed','Unexpected product; DR must not be replaced by R or FDR')
        if code in seen:raise SourceError('duplicate_product','Duplicate quotation product')
        seen.add(code)
        if record.get('date') not in (updated.strftime('%y-%m-%d'),expected_date):
            raise SourceError('date_mismatch','Row date disagrees with the quotation update date')
        rows.append({'product':code,'weighted_rate':numeric(record.get('weightedRate'),'weighted rate'),
                     'latest_rate':numeric(record.get('latestRate'),'latest rate'),
                     'average_term_days':numeric(record.get('avgPrd'),'average term'),
                     'updated_at':stamp})
    dr007=next((r for r in rows if r['product']=='DR007'),None)
    if not dr007 or dr007['weighted_rate'] is None or Decimal(dr007['weighted_rate'])<=0:
        raise SourceError('pending_dr007','No positive current-day DR007 weighted rate has been published')
    return {'updated_at':stamp,'quote_date':expected_date,'rows':rows}


def parse_history(raw,quote_date):
    try:text=raw.decode('utf-8-sig')
    except UnicodeError:raise SourceError('invalid_history','History is not UTF-8 CSV') from None
    result=[]
    for record in csv.reader(text.splitlines()):
        if not record or not any(cell.strip() for cell in record):continue
        if len(record)<9:raise SourceError('source_changed','History CSV no longer has its documented DR columns')
        try:day=date.fromisoformat(record[0]).isoformat()
        except ValueError:raise SourceError('invalid_history','History has an invalid date') from None
        if day>quote_date:raise SourceError('date_mismatch','History contains a date after the quotation day')
        result.append({'date':day,**{k:numeric(record[v],k) for k,v in HISTORY_COLUMNS.items()}})
    days=[r['date'] for r in result]
    if len(result)<2 or days!=sorted(set(days),reverse=True):
        raise SourceError('invalid_history','History must contain unique, descending source dates')
    result.reverse()
    present=[k for k in HISTORY_COLUMNS if any(r[k] is not None for r in result)]
    if not present:raise SourceError('invalid_history','No source curves are available')
    return {'start':result[0]['date'],'end':result[-1]['date'],'series':present,'rows':result}


def read_source(name,method,*,timeout=25,opener=urlopen):
    request=Request(BASE+name,data=b'' if method=='POST' else None,method=method,
                    headers={'User-Agent':'Mozilla/5.0','Referer':PAGE})
    try:
        with opener(request,timeout=timeout) as response:
            raw=response.read(MAX_BYTES+1)
            if len(raw)>MAX_BYTES:raise SourceError('source_changed','Source exceeds the size limit')
            return raw,{'url':BASE+name,'method':method,'http_date':response.headers.get('Date'),
                        'sha256':hashlib.sha256(raw).hexdigest()}
    except HTTPError as exc:
        raise SourceError('access_required' if exc.code in (401,403,429) else 'network_error',f'Source HTTP {exc.code}') from None
    except (URLError,TimeoutError,OSError):raise SourceError('network_error','Source did not complete its response') from None


def fetch_snapshot(output_dir,expected_date,*,timeout=25,opener=urlopen):
    """One quote request; history is requested once only after DR007 is ready."""
    date.fromisoformat(expected_date)
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    quotes_raw,quotes_meta=read_source('prr-md.json','GET',timeout=timeout,opener=opener)
    (out/'prr-md.json').write_bytes(quotes_raw)
    quote=parse_quotes(quotes_raw,expected_date)
    history_raw,history_meta=read_source('prr-chrt.csv','POST',timeout=timeout,opener=opener)
    (out/'prr-chrt.csv').write_bytes(history_raw)
    history=parse_history(history_raw,expected_date)
    result={'source':PAGE,'fetched_at':datetime.now(CHINA).isoformat(),'quotes':quote,'history':history,
            'source_requests':[quotes_meta,history_meta],'policy_rate':None,
            'history_is_separate_from_intraday_quotes':True}
    (out/'snapshot.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def read_replay(quote_path,history_path,expected_date):
    return {'source':PAGE,'fetched_at':None,'mode':'explicit_source_replay',
            'quotes':parse_quotes(Path(quote_path).read_bytes(),expected_date),
            'history':parse_history(Path(history_path).read_bytes(),expected_date),
            'policy_rate':None,'history_is_separate_from_intraday_quotes':True}
