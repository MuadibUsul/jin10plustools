"""Actually backfill absent ERP sessions from exact published inputs, not interpolation."""
from datetime import date,datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import hashlib

from .trading_calendar import sessions


def gdp_catalog(seed):
    """Keep dated historical GDP publications separate from the current year."""
    from .sources import read
    seed=Path(seed)
    historical=read(seed/'gdp-historical.json') if (seed/'gdp-historical.json').exists() else {}
    return {**historical,**read(seed/'gdp.json')}


def _available_gdp(table,stamp):
    row=table.get(str(date.fromisoformat(stamp).year-1))
    return row if row and row.get('published_on',stamp)<=stamp else None


def available_pe(output,*,history_path=None):
    from .engines.valuation.fetch_free_pe import _download,_parse_json,_timestamp_day,_number,HISTORY_URL,DETAIL_URL
    from .sources import read,save
    from .trading_calendar import completed_session
    completed=completed_session('xshg').isoformat()
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    previous={row['date']:row for row in read(history_path)} if history_path and Path(history_path).is_file() else {}
    result={day:Decimal(str(row['pe_ttm_mcw_raw'])) for day,row in previous.items()}
    evidence={day:{key:row[key] for key in ('source_file','source_sha256') if key in row} for day,row in previous.items()}
    errors=[];acquired={};succeeded=0
    for name,url in (('history',HISTORY_URL),('detail',DETAIL_URL)):
        try:
            raw=_download(url);sha=hashlib.sha256(raw).hexdigest()
            source_file=output/('pe-'+name+'-'+sha+'.json');source_file.write_bytes(raw)
            parsed=_parse_json(raw)
            if name=='history':
                records=parsed.get('index_eva_pe_growths')
                if not isinstance(records,list):raise ValueError('PE历史结构变化')
            else:
                if parsed.get('index_code')!='SH000300':raise ValueError('PE最新明细指标身份不符')
                records=[parsed]
            batch={}
            for row in records:
                day=_timestamp_day(row['ts']);value=_number(row['pe'])
                if day>completed:continue
                if day in batch:raise ValueError('PE补抓日期重复')
                if day in acquired and acquired[day]!=value:raise ValueError('PE最新明细与历史同日数值不一致')
                batch[day]=value
            acquired.update(batch);result.update(batch);succeeded+=1
            evidence.update({day:{'source_file':str(source_file.resolve()),'source_sha256':sha} for day in batch})
            normalized=[{'date':day,'pe_ttm_mcw_raw':str(result[day]),**evidence[day]} for day in sorted(result)]
            if history_path:save(history_path,normalized)
            save(output.parent/'pe-published-history.json',normalized)
        except (OSError,ValueError,RuntimeError,KeyError,TypeError) as exc:
            errors.append({'endpoint':name,'error':str(exc)})
    if errors:save(output/'acquisition-errors.json',errors)
    if not succeeded:raise ValueError('PE历史与最新明细均未取得；已保留既有证据')
    return result


def repair_erp(history_path,day,out,*,pe_fetcher=None,close_fetcher=None,bond_fetcher=None,pe_history=None,pe_values=None):
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
    pe=pe_values if pe_values is not None else pe_fetcher(out/'pe') if pe_fetcher else available_pe(out/'pe',history_path=pe_history)
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
        or _available_gdp(gdp_table,stamp)]]))[:max_requests]
    report={'missing_before':missing,'restored_dates':[],'raw_restored_dates':[],
            'requests':0,'unavailable_gdp_years':[],
            'unpublished_gdp_dates':[stamp for stamp in missing
                if gdp_table.get(str(date.fromisoformat(stamp).year-1)) and not _available_gdp(gdp_table,stamp)]}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    if not pending:return report
    closing={}
    for stamp in pending:
        if stamp!=day and stamp in {row['date'] for row in history}:continue
        previous=previous_session('xshg',stamp).isoformat()
        if stamp in inputs and stamp!=day:
            cap={'source':inputs[stamp]['source'],'rows':[inputs[stamp]]}
        else:
            report['requests']+=1
            try:cap=(cap_fetcher or fetch_all_a)(stamp,previous,out/('cap-'+stamp))
            except (OSError,ValueError,RuntimeError) as exc:
                report.setdefault('source_errors',[]).append({'date':stamp,'error':str(exc)});continue
        for row in cap['rows']:
            if row['date']<=day:
                inputs[row['date']]={**row,'source':cap['source']}
                if row['date'] in missing:report['raw_restored_dates'].append(row['date'])
        save(raw_path,[inputs[key] for key in sorted(inputs)])
        save(out/'all-a-history.json',[inputs[key] for key in sorted(inputs)])
        years={str(date.fromisoformat(row['date']).year-1) for row in cap['rows'] if row['date']<=day}
        report['unavailable_gdp_years'].extend(year for year in years if not gdp_table.get(year))
        if not any(_available_gdp(gdp_table,row['date']) for row in cap['rows'] if row['date']<=day):continue
        try:
            closes=(close_fetcher or fetch_close)(stamp,previous,out/('close-'+stamp),all_between=True)
            closing.update({row['date']:row['close'] for row in closes['rows']})
        except (OSError,ValueError,RuntimeError) as exc:
            report.setdefault('close_errors',[]).append({'date':stamp,'error':str(exc)});continue
        for row in cap['rows']:
            source_day=row['date']
            if source_day>day:continue
            if source_day not in closing:continue
            year=str(date.fromisoformat(source_day).year-1);gdp=_available_gdp(gdp_table,source_day)
            if not gdp:
                if year not in gdp_table:report['unavailable_gdp_years'].append(year)
                continue
            candidate={'date':source_day,'marketCap':row['market_cap_yi'],
                'gdp':gdp['value_yi'],'close':closing[source_day],'source':cap['source']}
            if gdp.get('published_on'):
                candidate.update(gdp_vintage=gdp,gdp_available_on_observation_date=True,
                    source_status='verified_published_inputs_historical_recalculation',
                    original_source_equivalence='not_asserted')
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
    report['unavailable_gdp_years']=sorted(set(report['unavailable_gdp_years']))
    report['remaining_raw_dates']=sorted(set(missing)-set(inputs))
    report['remaining_dates']=sorted(wanted-{row['date'] for row in history})
    save(out/'repair-report.json',report)
    return report


def rebuild_buffett_history(history_path,cap_path,day,gdp_table,out,*,close_fetcher=None,allow_later_published_gdp=False):
    """Recalculate missing sessions from actual caps, official closes and GDP.

    Old source rows remain unchanged. GDP must have a dated official publication
    no later than the observation; unavailable annual inputs stay unavailable.
    """
    from .sources import read,save
    from .engines.valuation.run_erp import fetch_close
    history=read(history_path);known={row['date'] for row in history}
    cap={row['date']:row for row in read(cap_path)}
    missing=set(sessions('xshg',min(known),day))-known
    eligible={};blocked={}
    for stamp in sorted(missing):
        gdp=gdp_table.get(str(date.fromisoformat(stamp).year-1))
        if stamp not in cap:blocked[stamp]='market_cap_unavailable';continue
        if not gdp or not gdp.get('published_on'):
            blocked[stamp]='dated_prior_year_GDP_unavailable';continue
        if gdp['published_on']>stamp and not allow_later_published_gdp:
            blocked[stamp]='prior_year_GDP_not_yet_published';continue
        eligible[stamp]=gdp
    report={'restored_dates':[],'blocked_dates':blocked,'close_requests':0}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    for year in sorted({stamp[:4] for stamp in eligible}):
        days=[stamp for stamp in eligible if stamp[:4]==year]
        capture=(close_fetcher or fetch_close)(max(days),min(days),out/('close-'+year),all_between=True)
        report['close_requests']+=1
        closes={row['date']:row for row in capture['rows']}
        for stamp in days:
            if stamp not in closes:report['blocked_dates'][stamp]='official_close_unavailable';continue
            gdp=eligible[stamp];row=cap[stamp]
            point_in_time=gdp['published_on']<=stamp
            history.append({'date':stamp,'marketCap':row['market_cap_yi'],'gdp':gdp['value_yi'],
                'close':closes[stamp]['close'],'source':row['source'] if point_in_time else row['source']+'|retrospective_gdp_v1',
                'gdp_vintage':gdp,'close_source':capture['source'],
                'gdp_available_on_observation_date':point_in_time,
                'close_evidence':capture.get('raw_evidence'),
                'source_status':'verified_published_inputs_historical_recalculation',
                'original_source_equivalence':'not_asserted'})
            report['restored_dates'].append(stamp)
        save(history_path,sorted(history,key=lambda row:row['date']))
        save(out/'repair-report.json',report)
    report['remaining_dates']=sorted(missing-set(report['restored_dates']))
    save(out/'repair-report.json',report)
    return report
