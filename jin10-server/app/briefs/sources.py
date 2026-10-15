"""Free-source adapters. Known method differences are explicit review warnings."""
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen

from .policy import previous_weekday
from . import central_bank

HERE=Path(__file__).parent


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp');temp.write_text(json.dumps(value,ensure_ascii=False,default=str),encoding='utf-8');temp.replace(path)


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def normalize_volume(payload,day):
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
    if len(result)<21 or result[-1]['date']!=day:raise ValueError('成交额目标日期尚未更新或历史不足')
    if len({r['date'] for r in result})!=len(result):raise ValueError('成交额日期重复')
    for i,r in enumerate(result):r['ma20']=sum((v['turn'] for v in result[i-19:i+1]),Decimal(0))/20 if i>=19 else None
    return result


def volume(day,out,state,refresh=False):
    url='https://mp-api.jin10.com/api/dynamic-data/child?'+urlencode({'page':1,'limit':100,'tb_name':'_vir_64','order':'date,desc'})
    request=Request(url,headers={'x-app-id':'fiXF2nOnDycGutVA','x-version':'1.0','Referer':'https://www.jin10.com/','User-Agent':'Mozilla/5.0'})
    with urlopen(request,timeout=25) as r:raw=r.read(4_000_001)
    if len(raw)>4_000_000:raise ValueError('成交额来源过大')
    (out/'volume-raw.json').write_bytes(raw)
    rows=normalize_volume(json.loads(raw,parse_float=Decimal),day);save(out/'plot-data.json',rows)
    return render_volume(rows,out)


def render_volume(rows,out):
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
            'facts':{'amount_yi':str(current['turn']),'ma20_yi':str(current['ma20']),'previous_date':prior['date'],'source':url_source()},'warnings':[]}


def url_source():return 'https://www.jin10.com/minipro/index.html#/pages_sub/huShenVolume/huShenVolume?category=hu_shen_volume&id=550059&type=4'


def turnover(day,out,state,refresh=False):
    # Standalone subprocess keeps legacy chart imports isolated from other engines.
    engine=HERE/'engines/turnover'
    command=[sys.executable,str(engine/'run.py'),'--expected-date',day,'--data-dir',str(out/'turnover-state'),'--no-ai']
    result=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',timeout=150)
    (out/'engine.log').write_text((result.stdout+'\n'+result.stderr)[-32000:],encoding='utf-8')
    if result.returncode:raise ValueError('换手率来源未更新、访问受限或制图失败；详情见任务原始日志')
    manifest=next((out/'turnover-state/outputs').glob('*/manifest.json'));data=read(manifest)
    folder=manifest.parent;rule=read(folder/'rules.json');chart=folder/data['chart'];shutil.copy2(chart,out/chart.name)
    raw=read(folder/'source.json');save(out/'source.json',raw)
    return {'title':rule['title'],'dynamic_body':rule['body'],'images':[chart.name],'data_date':day,
        'facts':read(folder/'facts.json'),'warnings':[]}


def liquidity(day,out,state,refresh=False):
    from .engines.liquidity.fetch_data import fetch_snapshot
    snapshot=fetch_snapshot(out,day)
    snapshot['central_bank']=central_bank.fetch(day,out)
    save(out/'liquidity-snapshot.json',snapshot)
    return render_liquidity(snapshot,out)


def render_liquidity(snapshot,out):
    from .engines.liquidity.render import render_poster
    from .engines.liquidity.copy_tracks import build_copy
    png,svg,audit=render_poster(snapshot,out,logo_path=HERE/'engines/liquidity/assets/plus-original.png')
    rule=build_copy(snapshot);bank=snapshot.get('central_bank');warning=[]
    if bank:
        dr=next(r for r in snapshot['quotes']['rows'] if r['product']=='DR007')
        spread=(Decimal(dr['weighted_rate'])-Decimal(bank['rate_percent']))*100
        rule['body']+=(f"较当日7天期逆回购操作利率{bank['rate_percent']}%{'低' if spread<0 else '高' if spread>0 else '持平'}"
                       +(f'{abs(spread):.2f}个基点。' if spread else '。')+
                       f"央行当日开展{bank['awarded_yi']}亿元7天期逆回购，投标量{bank['bid_yi']}亿元，中标量{bank['awarded_yi']}亿元。")
    else:warning.append('未取得对应日期央行操作信息，仅描述DR行情')
    save(out/'chart-audit.json',audit)
    return {'title':'【流动性指标】质押式回购利率走势与最新行情','dynamic_body':rule['body'],'images':[png.name],'data_date':snapshot['quotes']['quote_date'],
            'facts':{'quotes':snapshot['quotes'],'central_bank':bank,'history_range':[snapshot['history']['start'],snapshot['history']['end']]},'warnings':warning}


def oil_etf(day,out,state,refresh=False):
    from .engines.oil_etf.run import fetch
    snapshot=fetch(out,day);save(out/'oil-source.json',snapshot)
    history_path=state/'oil-history.json';history=read(history_path) if history_path.exists() else {}
    previous=previous_weekday(date.fromisoformat(day)).isoformat()
    funds=[];warnings=['USO掉期按当前最近月期货价格折算桶等值，属于测试估算口径，尚未与金十逐值对账']
    for fund in snapshot['funds']:
        futures=Decimal(fund['futures_barrels']);price=Decimal(fund['futures_contracts'][0]['reference_price_usd_per_barrel'])
        if price<=0:raise ValueError('期货参考价格无效')
        swap=Decimal(fund['swap_market_value_usd'])/price if fund['swaps'] else Decimal(0)
        total=futures+swap
        prior=history.get(previous,{}).get(fund['ticker'])
        comparable=prior and prior.get('method')=='nearest_future_swap_notional_v1'
        delta=total-Decimal(prior['barrels']) if comparable else None
        funds.append({'ticker':fund['ticker'],'barrels':str(total),'futures_barrels':str(futures),
                      'swap_barrels_estimate':str(swap),'reference_price':str(price),'change_barrels':str(delta) if delta is not None else None,
                      'method':'nearest_future_swap_notional_v1'})
        if not comparable:warnings.append(f"{fund['ticker']}缺少{previous}同口径快照，暂不判断单日增减")
    history[day]={f['ticker']:f for f in funds};save(history_path,history)
    def phrase(f):
        value=Decimal(f['barrels'])/10000
        label='名义敞口估算' if f['ticker']=='USO' else '期货持仓'
        part=f"{'WTI' if f['ticker']=='USO' else 'Brent'}（{f['ticker']}）{label}约{value:.2f}万桶"
        if f['change_barrels'] is not None:
            change=Decimal(f['change_barrels'])/10000
            part+=f"，较{previous}{'增加' if change>0 else '减少' if change<0 else '持平'}"+(f'{abs(change):.2f}万桶' if change else '')
        return part
    title='【原油ETF】美布两油ETF持仓数据更新'
    body=f"{day}，"+'；'.join(phrase(f) for f in funds)+'。'
    return {'title':title,'dynamic_body':body,'images':[],'data_date':day,
            'facts':{'funds':funds,'official_source':snapshot,'previous_date':previous},'warnings':warnings}


def valuation(day,out,state,refresh=False):
    from .engines.valuation.run_erp import run_erp
    from .engines.valuation.fetch_free_all_a import fetch_all_a
    from .charts import buffett_chart
    erp_history=state/'erp-history.json';bond_history=state/'bond-history.json'
    if not erp_history.exists():shutil.copy2(HERE/'seed/erp-history.json',erp_history)
    if not bond_history.exists():shutil.copy2(HERE/'seed/bond-history.json',bond_history)
    erp_out=out/'erp';result=run_erp(erp_history,erp_out,mode='free-official',expected_date=day,bond_history=bond_history,force_refresh=refresh)
    if not result.get('release_ready'):raise ValueError('ERP当前数据不足或制图失败')
    rows=read(erp_out/'数据快照.json');current,prior=rows[-1],rows[-2]
    # Preserve the full growing history; never restart daily updates from a fixed seed.
    for row in rows:row.pop('erp_cached',None)
    save(erp_history,rows)
    updated_bond=erp_out/'raw/bond/official-rolling-normalized.json'
    if updated_bond.exists():shutil.copy2(updated_bond,bond_history)
    prior_day=previous_weekday(date.fromisoformat(day)).isoformat()
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
    visible=[r for r in history if r['date']<=day];chart=buffett_chart(visible,out)
    save(out/'buffett-plot-data.json',visible);save(out/'erp-plot-data.json',rows)
    erp_image=Path(result['image_path']);shutil.copy2(erp_image,out/erp_image.name)
    from PIL import Image
    with Image.open(chart) as top,Image.open(erp_image) as bottom:
        if top.width!=bottom.width:raise ValueError('估值两图宽度不一致')
        combined=Image.new('RGB',(top.width,top.height+bottom.height),'black')
        combined.paste(top,(0,0));combined.paste(bottom,(0,top.height));combined.save(out/'股债性价比.png')
    ratio=Decimal(updated[day]['market_cap_yi'])/Decimal(gdp['value_yi'])*100
    erp=Decimal(current['erp_percent']);change=erp-Decimal(prior['erp_percent'])
    title=f"【A股温度计】股债性价比指标更新，沪深300ERP报{erp:.2f}%"
    body=(f"{day}，巴菲特指数（全A总市值/上年度全年GDP）为{ratio:.2f}%；"
          f"沪深300指数股权风险溢价（ERP）为{erp:.2f}%，较{prior['date']}变动{change:+.2f}个百分点，"
          f"{'高于' if erp>6 else '等于' if erp==6 else '低于'}6%参考线。权益资产相对国债的估值优势较前日{'增强' if change>0 else '减弱' if change<0 else '变化不大'}。")
    return {'title':title,'dynamic_body':body,'images':['股债性价比.png'],'data_date':day,
        'facts':{'buffett_percent':str(ratio),'gdp':gdp,'market_cap':cap,'erp_latest':current,'erp_previous':prior},
        'warnings':['巴菲特指数最新值采用东方财富全A上市公司总市值，历史保留原站序列，来源过渡待核对；未宣称与乐咕数值一致',
                    'ERP采用免费市值加权PE候选源和中债国债收益率曲线，已记录与原模板的来源差异']}


ADAPTERS={'volume':volume,'turnover':turnover,'valuation':valuation,'liquidity':liquidity,'oil_etf':oil_etf}
