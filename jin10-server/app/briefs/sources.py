"""Free-source adapters. Known method differences are explicit review warnings."""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from .policy import previous_weekday
from .delivery import default_notes
from . import central_bank
from .progress import record
from .trading_calendar import previous_session, require_window

HERE=Path(__file__).parent


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,default=str),encoding='utf-8');temp.replace(path)


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def normalize_volume(payload,day,*,required=True):
    rows=payload.get('data')
    if not isinstance(rows,list):raise ValueError('成交额来源结构变化')
    result=[]
    for r in sorted(rows,key=lambda r:r['date']):
        if r['date']>day:continue
        if all(r.get(k) is None for k in ('sh_turn','sz_turn','sh_vol','sz_vol')):continue
        if any(r.get(k) is None for k in ('sh_turn','sz_turn','sh_vol','sz_vol')):raise ValueError('沪深明细缺失')
        values=[Decimal(str(r[k])) for k in ('sh_turn','sz_turn','sh_vol','sz_vol')]
        if any(not v.is_finite() or v<=0 for v in values):raise ValueError('成交额存在无效值')
        result.append({'date':date.fromisoformat(r['date']).isoformat(),'turn':values[0]+values[1],'vol':values[2]+values[3]})
    if required and (len(result)<21 or result[-1]['date']!=day):raise ValueError('成交额目标日期尚未更新或历史不足')
    if len({r['date'] for r in result})!=len(result):raise ValueError('成交额日期重复')
    if required:require_window(result,'xshg',day,21,label='成交额20日均线及前日比较')
    for i,r in enumerate(result):r['ma20']=sum((v['turn'] for v in result[i-19:i+1]),Decimal(0))/20 if i>=19 else None
    return result


def volume(day,out,state,refresh=False):
    from .series_store import merge_history
    history_path=state/'volume-history.json'
    stored=read(history_path)['rows'] if history_path.is_file() else []
    incoming=[];seen=set()
    # Always refresh an overlapping year, and page back to the requested day on
    # historical runs. A shorter source response never replaces accumulated data.
    start=(date.fromisoformat(day)-timedelta(days=366)).isoformat()
    for page in range(1,51):
        url='https://mp-api.jin10.com/api/dynamic-data/child?'+urlencode({'page':page,'limit':100,'tb_name':'_vir_64','order':'date,desc'})
        request=Request(url,headers={'x-app-id':'fiXF2nOnDycGutVA','x-version':'1.0','Referer':'https://www.jin10.com/','User-Agent':'Mozilla/5.0'})
        with urlopen(request,timeout=25) as response:raw=response.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('成交额来源过大')
        (out/f'volume-raw-page-{page}.json').write_bytes(raw)
        batch=json.loads(raw,parse_float=Decimal).get('data')
        if not isinstance(batch,list):raise ValueError('成交额来源结构变化')
        dates={row['date'] for row in batch}
        if not batch:break
        if dates & seen:raise ValueError('成交额分页日期重复，停止不完整抓取')
        seen.update(dates);incoming.extend(batch)
        if min(dates)<=start or len(batch)<100:break
    else: raise ValueError('成交额达到分页上限，区间尚未覆盖')
    # Convert accumulated normalized points back to the source contract before
    # validating; only incoming exact dates replace older same-method values.
    fresh=normalize_volume({'data':incoming},day,required=False)
    all_rows={row['date']:row for row in stored}
    all_rows.update({row['date']:row for row in fresh})
    ordered=[all_rows[stamp] for stamp in sorted(all_rows) if stamp<=day]
    for index,row in enumerate(ordered):
        row['turn']=Decimal(str(row['turn']));row['vol']=Decimal(str(row['vol']))
        row['ma20']=sum((Decimal(str(value['turn'])) for value in ordered[index-19:index+1]),Decimal(0))/20 if index>=19 else None
    rows=ordered
    merge_history(history_path,rows,method='jin10_hushen_turnover_yi_v1')
    save(out/'plot-data.json',rows)
    if not any(row['date']==day for row in fresh):raise ValueError('成交额目标日期尚未更新；已补存可取得的真实历史')
    require_window(ordered,'xshg',day,21,label='成交额20日均线及前日比较')
    return render_volume(rows,out)


def render_volume(rows,out):
    record(out,3,'成交额数据校验通过，正在绘制历史走势')
    from .charts import volume_chart
    # Cached JSON encodes Decimal values as strings.
    for r in rows:
        for key in ('turn','vol','ma20'):
            if r.get(key) is not None:r[key]=Decimal(str(r[key]))
    current,prior=rows[-1],rows[-2];change=current['turn']-prior['turn'];percent=change/prior['turn']*100
    below=current['turn']<current['ma20'];streak=0
    for r in reversed(rows):
        if r['ma20'] is None or (r['turn']<r['ma20'])!=below:break
        streak+=1
    verb='回升' if change>0 else '回落' if change<0 else '持平'
    title=f"【A股温度计】两市成交额{verb}至{current['turn']/10000:.2f}万亿元"
    side='低于' if below else '不低于'
    body=(f"{current['date']}，沪深两市成交额为{current['turn']/10000:.2f}万亿元，较前一条有效交易日"
          f"{'增加' if change>=0 else '减少'}{abs(change):.2f}亿元，变动{percent:+.2f}%；"
          f"连续{streak}条有效交易日{side}20日均线（{current['ma20']/10000:.2f}万亿元）。"
          f"成交活跃度较前日{'回升' if change>0 else '回落' if change<0 else '持平'}。")
    image=volume_chart([r for r in rows if r['ma20'] is not None],out)
    return {'title':title,'dynamic_body':body,'images':[image.name],'data_date':current['date'],
            'facts':{'amount_yi':str(current['turn']),'amount_wan_yi':f"{current['turn']/10000:.2f}",'change_yi':f'{change:.2f}','change_pct':f'{percent:+.2f}','streak':streak,'side':side,'activity':'回升' if change>0 else '回落' if change<0 else '持平','ma20_yi':str(current['ma20']),'ma20_wan_yi':f"{current['ma20']/10000:.2f}",'previous_date':prior['date'],'source':url_source()},'warnings':[]}


def url_source():return 'https://www.jin10.com/minipro/index.html#/pages_sub/huShenVolume/huShenVolume?category=hu_shen_volume&id=550059&type=4'


def turnover(day,out,state,refresh=False):
    record(out,2,'正在读取换手率原始数据并生成双面板走势')
    # Standalone subprocess keeps legacy chart imports isolated from other engines.
    engine=HERE/'engines/turnover'
    from .engines.turnover.fetch_data import fetch_snapshot
    from .series_store import merge_history
    capture=fetch_snapshot(out/'turnover-fetch')
    fresh=read(capture['path'])
    merged=merge_history(state/'turnover-history.json',fresh,method='legu_count_ratios_ma9_v1')
    wanted=[row for row in merged if row['date']<=day]
    save(out/'source.json',wanted)
    require_window(wanted,'xshg',day,10,label='换手率9日均线')
    save(out/'turnover-merged.json',wanted)
    from .trading_calendar import sessions
    save(out/'turnover-calendar.json',{'sessions':sessions('xshg',wanted[0]['date'],day)})
    save(out/'turnover-config.json',{'calendar_file':str((out/'turnover-calendar.json').resolve())})
    command=[sys.executable,str(engine/'run.py'),'--expected-date',day,'--input',str(out/'turnover-merged.json'),
             '--data-dir',str(out/'turnover-state'),'--config',str(out/'turnover-config.json'),'--no-ai']
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=150)
    (out/'engine.log').write_text((result.stdout+'\n'+result.stderr)[-32000:],encoding='utf-8')
    if result.returncode:raise ValueError('换手率来源未更新、访问受限或制图失败；详情见任务原始日志')
    record(out,3,'换手率曲线与日期校验完成，正在整理图文')
    manifest=next((out/'turnover-state/replays').glob('*/manifest.json'));data=read(manifest)
    folder=manifest.parent;rule=read(folder/'rules.json');chart=folder/data['chart'];shutil.copy2(chart,out/chart.name)
    raw=read(folder/'source.json');save(out/'source.json',raw)
    require_window(raw['data'] if isinstance(raw,dict) else raw,'xshg',day,10,label='换手率9日均线')
    return {'title':rule['title'],'dynamic_body':rule['body'],'images':[chart.name],'data_date':day,
        'facts':read(folder/'facts.json'),'warnings':[],'turnover_watermark_removed':True}


def liquidity(day,out,state,refresh=False):
    from .engines.liquidity.fetch_data import fetch_snapshot
    snapshot=fetch_snapshot(out,day)
    from .series_store import merge_history
    path=state/'dr-history.json'
    before=read(path)['rows'] if path.exists() else []
    merged={row['date']:dict(row) for row in before}
    for row in snapshot['history'].get('rows',[]):
        existing=merged.setdefault(row['date'],{'date':row['date']})
        existing.update({key:value for key,value in row.items() if value is not None})
    rows=merge_history(path,list(merged.values()),method='cfets_dr_close_v1')
    for row in rows:
        for code in ('DR001','DR007','DR014'):row.setdefault(code,None)
    snapshot['history']['rows']=[row for row in rows if row['date']<=day]
    if snapshot['history']['rows']:snapshot['history']['start']=snapshot['history']['rows'][0]['date']
    record(out,2,f"中国货币网行情已读取，源数据时间 {snapshot['quotes']['updated_at']}；正在核对央行公告")
    warnings=[]
    try:
        snapshot['central_bank']=central_bank.fetch(day,out)
    except ValueError as exc:
        # The DR007 brief still has value when no reverse-repo table was published.
        snapshot['central_bank']=None
        warnings.append(f'央行公开市场公告未纳入：{exc}')
    save(out/'liquidity-snapshot.json',snapshot)
    payload=render_liquidity(snapshot,out)
    payload['warnings']=warnings+payload.get('warnings',[])
    return payload


def render_liquidity(snapshot,out,notes=None):
    record(out,3,'行情与历史序列已校验，正在绘制流动性图片')
    from .engines.liquidity.render import render_poster
    from .engines.liquidity.copy_tracks import build_copy
    notes=os.getenv('CATEGORY_NOTES',default_notes('liquidity')) if notes is None else notes
    png,svg,audit=render_poster(snapshot,out,logo_path=HERE/'engines/liquidity/assets/plus-original.png',notes=notes)
    rule=build_copy(snapshot);bank=snapshot.get('central_bank');warning=[]
    if bank:
        dr=next(r for r in snapshot['quotes']['rows'] if r['product']=='DR007')
        spread=(Decimal(dr['weighted_rate'])-Decimal(bank['rate_percent']))*100
        rule['body']+=(f"较当日7天期逆回购操作利率{bank['rate_percent']}%{'低' if spread<0 else '高' if spread>0 else '持平'}"
                       +(f'{abs(spread):.2f}个基点。' if spread else '。')+
                       f"央行当日开展{bank['awarded_yi']}亿元7天期逆回购，投标量{bank['bid_yi']}亿元，中标量{bank['awarded_yi']}亿元。")
    else:warning.append('未取得对应日期央行操作信息，仅描述DR行情')
    save(out/'chart-audit.json',audit)
    # Additive evidence for editorial copy; no history is inferred from prose.
    previous_close = {}
    for product in ('DR001', 'DR007'):
        prior = next((row for row in reversed(snapshot['history'].get('rows', []))
                      if row['date'] < snapshot['quotes']['quote_date'] and row.get(product) is not None), None)
        if prior:
            previous_close[product] = {'date': prior['date'], 'weighted_rate': str(prior[product])}
    return {'title':'【流动性指标】质押式回购利率走势与最新行情','dynamic_body':rule['body'],'images':[png.name],'data_date':snapshot['quotes']['quote_date'],
            'fixed_notes':notes,'chart_notes':notes,
            'facts':{'quotes':snapshot['quotes'],'central_bank':bank,'history_range':[snapshot['history']['start'],snapshot['history']['end']],
                     'previous_close':previous_close},'warnings':warning}


def oil_etf(day,out,state,refresh=False):
    record(out,2,'正在读取USO与BNO发行方持仓及前日持仓证据')
    from .engines.oil_etf.run import fetch
    from .engines.oil_etf.previous import PreviousSourceError,fetch as fetch_previous
    snapshot=fetch(out,day);save(out/'oil-source.json',snapshot)
    history_path=state/'oil-history.json';history=read(history_path) if history_path.exists() else {}
    previous=previous_session('xnys',day).isoformat()
    prior=history.get(previous,{})
    previous_source=None
    warnings=['USO掉期桶等值按期货价格估算；日变化仅比较期货合约，尚未覆盖掉期变化']
    if any(not prior.get(ticker,{}).get('futures_barrels') for ticker in ('USO','BNO')):
        try:
            previous_source=fetch_previous(previous,state/'source-archive')
            save(out/'oil-previous-source.json',previous_source)
            for ticker,prior_record in previous_source['funds'].items():
                if not prior.get(ticker,{}).get('futures_barrels'):
                    prior[ticker]={'futures_barrels':prior_record['futures_barrels'],
                                   'method':'futures_contracts_1000',
                                   'source_url':prior_record['source_url'],
                                   'source_sha256':prior_record['raw_sha256']}
            history[previous]=prior
        except PreviousSourceError as exc:
            warnings.append(f'{previous}公开持仓回溯未通过日期与数量校验：{exc}')
    if any(record.get('source_url','').startswith('https://companiesmarketcap.com/') for record in prior.values()):
        warnings.append(f'前日持仓来源：CompaniesMarketCap（{previous}），原始网址见数据核对与来源')
    funds=[]
    for fund in snapshot['funds']:
        futures=Decimal(fund['futures_barrels']);price=Decimal(fund['futures_contracts'][0]['reference_price_usd_per_barrel'])
        if price<=0:raise ValueError('期货参考价格无效')
        swap=Decimal(fund['swap_market_value_usd'])/price if fund['swaps'] else Decimal(0)
        total=futures+swap
        prior_fund=prior.get(fund['ticker'])
        prior_futures=prior_fund.get('futures_barrels') if prior_fund else None
        delta=futures-Decimal(prior_futures) if prior_futures is not None else None
        funds.append({'ticker':fund['ticker'],'barrels':str(total),'futures_barrels':str(futures),
                      'swap_barrels_estimate':str(swap),'reference_price':str(price),'change_barrels':str(delta) if delta is not None else None,
                      'change_basis':'futures_contracts_1000' if delta is not None else None,
                      'method':'nearest_future_swap_notional_v1'})
        if delta is None:warnings.append(f"{fund['ticker']}缺少{previous}期货持仓快照，暂不判断单日增减")
    history[day]={f['ticker']:f for f in funds};save(history_path,history)
    record(out,3,'持仓单位和前日变化已核对')
    def phrase(f):
        value=Decimal(f['barrels'])/10000
        label='名义敞口估算' if f['ticker']=='USO' else '期货持仓'
        part=f"{'WTI' if f['ticker']=='USO' else 'Brent'}（{f['ticker']}）{label}约{value:.2f}万桶"
        if f['change_barrels'] is not None:
            change=Decimal(f['change_barrels'])/10000
            part+=f"，期货持仓较{previous}{'增加' if change>0 else '减少' if change<0 else '持平'}"+(f'{abs(change):.2f}万桶' if change else '')
        return part
    title='【原油ETF】美布两油ETF持仓数据更新'
    body=f"{day}，"+'；'.join(phrase(f) for f in funds)+'。'
    return {'title':title,'dynamic_body':body,'images':[],'data_date':day,
            'facts':{'funds':funds,'official_source':snapshot,'previous_date':previous,
                     'previous_source':previous_source or prior},'warnings':warnings}


def valuation(day,out,state,refresh=False):
    from .engines.valuation.run_erp import run_erp
    from .engines.valuation.fetch_free_all_a import fetch_all_a
    from .charts import valuation_chart
    erp_history=state/'erp-history.json';bond_history=state/'bond-history.json'
    if not erp_history.exists():shutil.copy2(HERE/'seed/erp-history.json',erp_history)
    if not bond_history.exists():shutil.copy2(HERE/'seed/bond-history.json',bond_history)
    buffett_path=state/'buffett-history.json'
    if not buffett_path.exists():shutil.copy2(HERE/'seed/buffett-history.json',buffett_path)
    from .valuation_repair import repair_buffett
    try:
        repair_buffett(buffett_path,day,out/'buffett-history-repair',read(HERE/'seed/gdp.json'))
    except (OSError,ValueError,RuntimeError) as exc:
        save(out/'buffett-history-repair-error.json',{'error':str(exc)})
    save(out/'buffett-plot-data.json',[row for row in read(buffett_path) if row['date']<=day])
    if (state/'all-a-history.json').exists():
        shutil.copy2(state/'all-a-history.json',out/'all-a-history.json')
    from .valuation_repair import repair_erp
    try:
        repair_report=repair_erp(erp_history,day,out/'erp-history-repair',pe_history=state/'pe-published-history.json')
        if (state/'pe-published-history.json').exists():
            shutil.copy2(state/'pe-published-history.json',out/'pe-published-history.json')
        record(out,2,f"已补回{len(repair_report['restored_dates'])}个ERP历史交易日")
    except (OSError,ValueError,RuntimeError) as exc:
        # Keep every completed chunk. Current input acquisition may still succeed
        # even when an older PE is absent from the public source's sampled table.
        save(out/'erp-history-repair-error.json',{'error':str(exc),'current_generation_continues':True})
    if (state/'pe-published-history.json').exists():
        shutil.copy2(state/'pe-published-history.json',out/'pe-published-history.json')
    erp_out=out/'erp';updated_bond=erp_out/'raw/bond/official-rolling-normalized.json'
    try:
        result=run_erp(erp_history,erp_out,mode='free-official',expected_date=day,bond_history=bond_history,
                       force_refresh=refresh or os.getenv('MANUAL_GENERATION')=='1',
                       progress=lambda message:record(out,2,message))
    finally:
        # Keep the bond correction even if a later PE/close step fails; the next
        # successful valuation build must start from the corrected treasury series.
        if updated_bond.exists():shutil.copy2(updated_bond,bond_history)
    if not result.get('release_ready'):raise ValueError('ERP当前数据不足或制图失败')
    rows=read(erp_out/'数据快照.json');current,prior=rows[-1],rows[-2]
    # Preserve the full growing history; never restart daily updates from a fixed seed.
    for row in rows:row.pop('erp_cached',None)
    save(erp_history,rows)
    prior_day=previous_session('xshg',day).isoformat()
    record(out,2,'ERP数据已核验，正在读取A股总市值及GDP分母')
    cap=fetch_all_a(day,prior_day,out/'all-a')
    originals=read(HERE/'seed/buffett-history.json')
    history_path=state/'buffett-history.json'
    history=read(history_path) if history_path.exists() else originals
    gdp_table=read(HERE/'seed/gdp.json')
    gdp=gdp_table.get(str(date.fromisoformat(day).year-1))
    if not gdp:raise ValueError('上年度GDP尚未配置，不能沿用过期分母')
    updated={r['date']:r for r in cap['rows']}
    # Keep all historical source records, including original category duplicates.
    history=[r for r in history if r['date'] not in updated]
    for r in cap['rows']:
        close=next((x['close'] for x in rows if x['date']==r['date']),None)
        if close is None:raise ValueError('估值图缺少对应日期沪深300点位')
        history.append({'date':r['date'],'marketCap':r['market_cap_yi'],'gdp':gdp['value_yi'],'close':close,'source':cap['source']})
    history.sort(key=lambda r:r['date']);save(history_path,history)
    visible=[r for r in history if r['date']<=day]
    save(out/'buffett-plot-data.json',visible);save(out/'erp-plot-data.json',rows)
    erp_image=Path(result['image_path']);shutil.copy2(erp_image,out/erp_image.name)
    record(out,3,'估值输入数据已核验，正在按冻结模板绘制双面板')
    valuation_chart(visible,rows,out)
    ratio=Decimal(updated[day]['market_cap_yi'])/Decimal(gdp['value_yi'])*100
    erp=Decimal(current['erp_percent']);change=erp-Decimal(prior['erp_percent'])
    title=f"【A股温度计】股债性价比指标更新，沪深300ERP报{erp:.2f}%"
    body=(f"{day}，巴菲特指数（A股总市值/上年度全年GDP）为{ratio:.2f}%；"
          f"沪深300指数股权风险溢价（ERP）为{erp:.2f}%，较{prior['date']}变动{change:+.2f}个百分点，"
          f"{'高于' if erp>6 else '等于' if erp==6 else '低于'}6%参考线。权益资产相对国债的估值优势较前日{'增强' if change>0 else '减弱' if change<0 else '变化不大'}。")
    warnings=['巴菲特指数最新值采用东方财富全A上市公司总市值，历史保留原站序列，来源过渡待核对；未宣称与乐咕数值一致',
              'ERP采用免费市值加权PE候选源和中债国债收益率曲线，已记录与原模板的来源差异']
    revision=result.get('bond_revision_check') or {}
    corrections=revision.get('corrections') or []
    if corrections:
        detail='、'.join(f"{item['date']} {item['old_yield']}→{item['new_yield']}" for item in corrections)
        warnings.append(f'中债十年期国债收益率历史数据已按官方来源修正：{detail}。')
    return {'title':title,'dynamic_body':body,'images':['股债性价比.png'],'data_date':day,
        'unified_valuation':True,
        'facts':{'buffett_percent':str(ratio),'gdp':gdp,'market_cap':cap,'erp_latest':current,'erp_previous':prior,
                 'bond_revision_check':revision},
        'warnings':warnings}


def us_barometer(day,out,state,refresh=False):
    """US barometer: Wilshire 5000 / quarterly GDP and StreetStats ERP."""
    record(out,2,'正在获取美股总市值、季度GDP与股权风险溢价')
    from .engines.us_barometer.run import SNAPSHOT,fetch,render
    from .series_store import merge_history
    partial={}
    methods={'wilshire':'yahoo_w5000_v1','erp':'streetstats_erp_v1','gdp':'fred_current_revised'}
    def captured(name,rows):
        key='period' if name=='gdp' else 'date'
        rows=[row for row in rows if row[key]<=day]
        partial[name]=merge_history(state/(name+'-history.json'),rows,method=methods[name],key=key)
        save(out/SNAPSHOT,{'target':day,**partial,'sources':{}})
    snapshot=fetch(day,out,allow_gap_repair=True,on_series=captured)
    for series in ('wilshire','erp','gdp'):
        snapshot[series]=merge_history(state/(series+'-history.json'),snapshot[series],
            method={'wilshire':'yahoo_w5000_v1','erp':'streetstats_erp_v1','gdp':'fred_current_revised'}[series],
            key='period' if series=='gdp' else 'date')
    from .engines.us_structure.fetch_data import repair as repair_us_series
    snapshot['wilshire'],repair_report=repair_us_series('wilshire',snapshot['wilshire'],day,
        checkpoint=lambda rows:merge_history(state/'wilshire-history.json',rows,method='yahoo_w5000_v1'))
    save(out/'wilshire-repair.json',repair_report)
    from .engines.us_barometer.run import window
    for name in ('wilshire','erp'):snapshot[name]=window(snapshot[name],day)
    save(out/SNAPSHOT,snapshot)
    if snapshot.get('historical_vintage_unverified'):
        raise ValueError('历史GDP公布版本未核验：已保留来源数据，不生成冒充当时可得数据的历史稿')
    payload=render(snapshot,out)
    record(out,3,'三项指标与分级已核对，两张图表已生成')
    return payload


def us_structure(day,out,state,refresh=False):
    """US market structure: index changes, equal-weight gap and Russell 2000."""
    record(out,2,'正在获取美股指数与市场宽度数据')
    from .engines.us_structure.run import SNAPSHOT,fetch,render
    from .series_store import merge_history
    partial={}
    def captured(name,rows):
        rows=[row for row in rows if row['date']<=day]
        partial[name]=merge_history(state/(name+'-history.json'),rows,method='yahoo_daily_close_low_v1')
        save(out/SNAPSHOT,{'target':day,'data':partial,'sources':{'yahoo':'https://query1.finance.yahoo.com/v8/finance/chart/'}})
    snapshot=fetch(day,out,allow_gap_repair=True,on_series=captured)
    from .engines.us_structure.fetch_data import repair as repair_us_series
    for series,rows in snapshot['data'].items():
        snapshot['data'][series]=merge_history(state/(series+'-history.json'),rows,method='yahoo_daily_close_low_v1')
        snapshot['data'][series],repair_report=repair_us_series(series,snapshot['data'][series],day,
            checkpoint=lambda repaired,name=series:merge_history(state/(name+'-history.json'),repaired,method='yahoo_daily_close_low_v1'))
        merge_history(state/(series+'-history.json'),snapshot['data'][series],method='yahoo_daily_close_low_v1')
        save(out/(series+'-repair.json'),repair_report)
    from .engines.us_structure.run import window
    snapshot['data']={name:window(rows,day) for name,rows in snapshot['data'].items()}
    save(out/SNAPSHOT,snapshot)
    payload=render(snapshot,out)
    record(out,3,'市场结构与小盘股数据已核对，图表已生成')
    return payload


from .ai_compute import token_cost,gpu_rental
ADAPTERS={'volume':volume,'turnover':turnover,'valuation':valuation,'liquidity':liquidity,'oil_etf':oil_etf,
          'us_barometer':us_barometer,'us_structure':us_structure,
          'token_cost':token_cost,'gpu_rental':gpu_rental}
