"""Actually backfill absent ERP sessions from exact published inputs, not interpolation."""
from datetime import date,datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import hashlib

from .trading_calendar import sessions


def available_pe(output,*,history_path=None):
    from .engines.valuation.fetch_free_pe import _download,_parse_json,_timestamp_day,_number,HISTORY_URL,DETAIL_URL
    from .sources import read,save
    from .policy import CHINA
    from .trading_calendar import is_session,previous_session
    now=datetime.now(CHINA)
    completed=(now.date() if now.hour>=16 and is_session('xshg',now.date())
               else previous_session('xshg',now.date())).isoformat()
    raw=_download(HISTORY_URL)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    history_sha=hashlib.sha256(raw).hexdigest()
    history_file=output/('pe-backfill-'+history_sha+'.json');history_file.write_bytes(raw)
    records=_parse_json(raw).get('index_eva_pe_growths')
    if not isinstance(records,list):raise ValueError('PE历史结构变化')
    result={};evidence={}
    for row in records:
        day=_timestamp_day(row['ts']);value=_number(row['pe'])
        if day>completed:continue
        if day in result:raise ValueError('PE补抓日期重复')
        result[day]=value
        evidence[day]={'source_file':str(history_file.resolve()),'source_sha256':history_sha}
    # The history endpoint samples older sessions. Preserve its independently
    # dated latest-detail value every day before that day disappears from history.
    detail_raw=_download(DETAIL_URL)
    detail_sha=hashlib.sha256(detail_raw).hexdigest()
    detail_file=output/('pe-detail-'+detail_sha+'.json');detail_file.write_bytes(detail_raw)
    detail=_parse_json(detail_raw)
    if detail.get('index_code')!='SH000300':raise ValueError('PE最新明细指标身份不符')
    stamp=_timestamp_day(detail['ts']);value=_number(detail['pe'])
    if stamp in result and result[stamp]!=value:raise ValueError('PE最新明细与历史同日数值不一致')
    if stamp<=completed:
        result[stamp]=value
        evidence[stamp]={'source_file':str(detail_file.resolve()),'source_sha256':detail_sha}
    before=read(history_path) if history_path and Path(history_path).is_file() else []
    merged={row['date']:Decimal(str(row['pe_ttm_mcw_raw'])) for row in before}
    merged.update(result)
    previous={row['date']:row for row in before}
    normalized=[{'date':day,'pe_ttm_mcw_raw':str(merged[day]),
                 **(evidence.get(day) or {key:previous[day][key] for key in ('source_file','source_sha256')
                    if key in previous.get(day,{})})} for day in sorted(merged)]
    if history_path:save(history_path,normalized)
    save(output.parent/'pe-published-history.json',normalized)
    result=merged
    return result


def repair_erp(history_path,day,out,*,pe_fetcher=None,close_fetcher=None,bond_fetcher=None,pe_history=None):
    from .sources import read,save
    from .engines.valuation.run_erp import fetch_close
    from .engines.valuation.fetch_free_bond import fetch_bond_history,METHOD_ID
    from .engines.valuation.calculate import calculate_row
    rows=read(history_path);existing={row['date']:row for row in rows}
    start=min(existing)
    wanted=set(sessions('xshg',start,day))
    missing=sorted(wanted-set(existing))
    report={'requested_start':start,'requested_end':day,'missing_before':missing,'restored_dates':[]}
    if not missing:return report
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    pe=pe_fetcher(out/'pe') if pe_fetcher else available_pe(out/'pe',history_path=pe_history)
    candidates=[stamp for stamp in missing if stamp in pe]
    # Source downsampling is explicit. A missing source PE is never substituted
    # with a different weighted PE definition or an interpolated value.
    report['source_pe_unavailable']=[stamp for stamp in missing if stamp not in pe]
    # Work in bounded calendar-year chunks; persist each completed chunk so an
    # interruption resumes at the remaining actual gaps.
    for year in sorted({stamp[:4] for stamp in candidates})[:3]:
        days=[stamp for stamp in candidates if stamp[:4]==year]
        close=(close_fetcher or fetch_close)(max(days),min(days),out/('close-'+year),all_between=True)
        bonds=(bond_fetcher or fetch_bond_history)(min(days),max(days),out/('bond-'+year))
        closes={row['date']:row for row in close['rows']}
        yields={row['date']:row for row in bonds['normalized']}
        for stamp in days:
            if stamp not in closes or stamp not in yields:continue
            raw=Decimal(str(pe[stamp]));rounded=raw.quantize(Decimal('.01'),rounding=ROUND_HALF_UP)
            existing[stamp]=calculate_row({'date':stamp,'close':closes[stamp]['close'],
                'pe_ttm_mcw':str(rounded),'pe_ttm_mcw_raw':str(raw),
                'bond_yield_number':yields[stamp]['yield_source_text'],
                'close_source':'CSI_official_index_performance','pe_source':'snowball_fund_danjuan_public',
                'bond_source':METHOD_ID,'source_status':'verified_free_source_history_backfill'})
            report['restored_dates'].append(stamp)
        save(history_path,[existing[stamp] for stamp in sorted(existing)])
    report['remaining_dates']=sorted(wanted-set(existing))
    save(out/'repair-report.json',report)
    return report


def repair_buffett(history_path,day,out,gdp_table,*,cap_fetcher=None,close_fetcher=None,max_requests=4):
    """Fetch missing market-cap sessions independently of PE availability."""
    from .sources import read,save
    from .engines.valuation.fetch_free_all_a import fetch_all_a
    from .engines.valuation.run_erp import fetch_close
    from .trading_calendar import previous_session
    history=read(history_path);known={row['date'] for row in history}
    wanted=set(sessions('xshg',min(known),day));missing=sorted(wanted-known)
    raw_path=Path(history_path).parent/'all-a-history.json'
    inputs={row['date']:row for row in read(raw_path)} if raw_path.exists() else {}
    pending=list(dict.fromkeys([day,*[stamp for stamp in missing if stamp not in inputs
        or gdp_table.get(str(date.fromisoformat(stamp).year-1))]]))[:max_requests]
    report={'missing_before':missing,'restored_dates':[],'raw_restored_dates':[],
            'requests':0,'unavailable_gdp_years':[]}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if not pending:return report
    closing={}
    for stamp in pending:
        if stamp!=day and stamp in {row['date'] for row in history}:continue
        previous=previous_session('xshg',stamp).isoformat()
        if stamp in inputs and stamp!=day:
            cap={'source':inputs[stamp]['source'],'rows':[inputs[stamp]]}
        else:
            cap=(cap_fetcher or fetch_all_a)(stamp,previous,out/('cap-'+stamp))
            report['requests']+=1
        for row in cap['rows']:
            if row['date']<=day:
                inputs[row['date']]={**row,'source':cap['source']}
                if row['date'] in missing:report['raw_restored_dates'].append(row['date'])
        save(raw_path,[inputs[key] for key in sorted(inputs)])
        save(out/'all-a-history.json',[inputs[key] for key in sorted(inputs)])
        try:
            closes=(close_fetcher or fetch_close)(stamp,previous,out/('close-'+stamp),all_between=True)
            closing.update({row['date']:row['close'] for row in closes['rows']})
        except (OSError,ValueError,RuntimeError) as exc:
            report.setdefault('close_errors',[]).append({'date':stamp,'error':str(exc)});continue
        for row in cap['rows']:
            source_day=row['date']
            if source_day>day:continue
            if source_day not in closing:continue
            year=str(date.fromisoformat(source_day).year-1);gdp=gdp_table.get(year)
            if not gdp:report['unavailable_gdp_years'].append(year);continue
            candidate={'date':source_day,'marketCap':row['market_cap_yi'],
                'gdp':gdp['value_yi'],'close':closing[source_day],'source':cap['source']}
            # Retain original provider/category duplicates; replace only an exact
            # date in the same newly fetched source definition.
            same_source=any(old['date']==source_day and old.get('source')==cap['source'] for old in history)
            if source_day not in known or same_source:
                history=[old for old in history if not (old['date']==source_day and old.get('source')==cap['source'])]
                history.append(candidate)
                if source_day in missing:report['restored_dates'].append(source_day)
        save(history_path,sorted(history,key=lambda row:row['date']))
        save(out/'all-a-history.json',[inputs[key] for key in sorted(inputs)])
    report['restored_dates']=sorted(set(report['restored_dates']))
    report['raw_restored_dates']=sorted(set(report['raw_restored_dates']))
    report['remaining_raw_dates']=sorted(set(missing)-set(inputs))
    report['remaining_dates']=sorted(wanted-{row['date'] for row in history})
    save(out/'repair-report.json',report)
    return report
