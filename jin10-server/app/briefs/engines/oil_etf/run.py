"""Free official USCF oil ETF data collector. Python standard library only."""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import date,datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.request import Request,urlopen
try:
    from .delivery import write_delivery
except ImportError:
    from delivery import write_delivery

CHINA=timezone(timedelta(hours=8))
CONFIG='https://www.uscfinvestments.com/site-template/assets/javascript/api_key.php'
API='https://secure.alpsinc.com/MarketingAPI/api/v1/'
FUNDS=('USO','BNO')
HERE=Path(__file__).resolve().parent
SPECS={'USO':('WTI CRUDE FUTURE',r'CL[FGHJKMNQUVXZ]\d{1,2}'),
       'BNO':('BRENT CRUDE FUTR',r'CO[FGHJKMNQUVXZ]\d{1,2}')}


class DataError(Exception):
    def __init__(self,code,message):super().__init__(message);self.code=code


def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)


def decimal(value,field):
    if value is None or isinstance(value,bool):raise DataError('invalid_data',f'Missing or invalid {field}')
    try:v=Decimal(str(value))
    except InvalidOperation:raise DataError('invalid_data',f'Invalid {field}') from None
    if not v.is_finite():raise DataError('invalid_data',f'Nonfinite {field}')
    return v


def load(raw):
    try:return json.loads(raw,parse_float=Decimal,parse_int=Decimal)
    except (ValueError,UnicodeError):raise DataError('source_changed','Invalid source JSON') from None


def source_day(value):
    try:return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError:raise DataError('invalid_date','Invalid source date') from None


def normalize(ticker,holdings_raw,price_raw,expected=None):
    holdings=load(holdings_raw);prices=load(price_raw)
    if not isinstance(holdings,list) or not holdings or not isinstance(prices,list) or len(prices)!=1:
        raise DataError('source_changed','Missing holdings or ambiguous daily price')
    if not all(isinstance(r,dict) and r.get('fundsymbol')==ticker and r.get('fullpartial')=='full' for r in holdings):
        raise DataError('source_changed','Unexpected fund or partial holdings')
    days={source_day(r.get('asofdate')) for r in holdings}
    if len(days)!=1:raise DataError('date_mismatch','Mixed dates in holdings')
    day=days.pop();price=prices[0]
    if price.get('symbol')!=ticker or source_day(price.get('displaydate'))!=day:
        raise DataError('date_mismatch','Holdings and daily price dates differ')
    if day>datetime.now(CHINA).date().isoformat():raise DataError('invalid_date','Future data')
    if expected and day!=expected:raise DataError('pending_update','Source does not yet match the requested data date')
    contracts=[];swaps=[];other=[];barrels=Decimal(0);swap_value=Decimal(0);seen=set()
    for row in holdings:
        kind=row.get('holdingtype');symbol=row.get('identifiertodisplay') or row.get('primaryidentifier') or ''
        name=row.get('name')
        key=(kind,symbol,name,row.get('possessionname'))
        if key in seen:raise DataError('invalid_data','Duplicate holding')
        seen.add(key)
        if row.get('possessionname')!='Hold':
            raise DataError('source_changed','Unsupported holding possession; retain raw data for review')
        if kind=='Futures':
            prefix,pattern=SPECS[ticker]
            if not isinstance(name,str) or not name.startswith(prefix) or not re.fullmatch(pattern,symbol):
                raise DataError('contract_changed','Unknown futures contract; multiplier needs review')
            quantity=decimal(row.get('shares'),'contracts')
            if quantity!=quantity.to_integral_value() or quantity<0:raise DataError('invalid_data','Invalid long futures quantity')
            count=quantity*1000;barrels+=count
            contracts.append({'name':name,'ticker':symbol,'quantity_contracts':str(quantity),
                'barrels_per_contract':'1000','barrels':str(count),
                'reference_price_usd_per_barrel':str(decimal(row.get('contractprice'),'contractprice')),
                'market_value_usd':str(decimal(row.get('marketvalue'),'marketvalue'))})
        elif kind=='Swap':
            value=decimal(row.get('marketvalue'),'swap marketvalue');swap_value+=value
            swaps.append({'name':name,'ticker':symbol,'quantity_source':str(decimal(row.get('shares'),'swap shares')),
                'market_value_usd':str(value),'reference_price_source':str(decimal(row.get('contractprice'),'swap contractprice'))})
        elif kind not in {'Government Debt','Cash','Cash Equivalent'}:
            other.append({'name':name,'type':kind})
    if not contracts:raise DataError('source_changed','No supported crude futures found')
    total=str(barrels) if not swaps and not other else None
    return {'ticker':ticker,'underlying':'WTI' if ticker=='USO' else 'Brent','data_date':day,
        'source_page':f'https://www.uscfinvestments.com/holdings/{ticker.lower()}',
        'nav_usd':str(decimal(price.get('nav'),'nav')),
        'net_assets_usd':str(decimal(price.get('navtotal'),'navtotal')),
        'futures_barrels':str(barrels),'futures_ten_thousand_barrels':str(barrels/10000),
        'futures_contracts':contracts,'swaps':swaps,'swap_market_value_usd':str(swap_value),
        'total_barrels':total,'total_barrels_status':'futures_only_no_swaps' if total else 'not_comparable_without_swap_method',
        'other_derivatives':other,'daily_change_barrels':None,
        'daily_change_status':'previous_daily_holdings_not_supplied',
        'jin10_total_exposure_verified':False}


def request(url,token=None,opener=urlopen):
    headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.uscfinvestments.com/'}
    if token:headers['Authorization']='Bearer '+token
    try:
        with opener(Request(url,headers=headers),timeout=25) as response:
            raw=response.read(4_000_001)
            if len(raw)>4_000_000:raise DataError('source_changed','Response exceeds size limit')
            return raw
    except HTTPError as exc:
        raise DataError('access_required' if exc.code in (401,403,429) else 'network_error',f'Source HTTP {exc.code}') from None
    except (URLError,TimeoutError,OSError):raise DataError('network_error','Source request failed') from None


def fetch(out,expected=None):
    # Same public visitor configuration used by the official page. Never save/log token.
    config=request(CONFIG).decode('utf-8')
    match=re.search(r'var token\s*=\s*[\x22\x27]([^\x22\x27]+)[\x22\x27]',config)
    if not match:raise DataError('source_changed','Public visitor configuration changed')
    token=match.group(1);funds=[];hashes={}
    for ticker in FUNDS:
        data=[]
        for path in (f'holding/{ticker}/full',f'dailyprice/{ticker}'):
            raw=request(API+path,token);name=path.replace('/','-')+'.json'
            (out/name).write_bytes(raw);hashes[name]=hashlib.sha256(raw).hexdigest();data.append(raw)
        funds.append(normalize(ticker,*data,expected))
    if len({r['data_date'] for r in funds})!=1:raise DataError('pending_update','Fund dates differ')
    return {'provider':'USCF official public holdings','fetched_at':datetime.now(CHINA).isoformat(),
        'data_date':funds[0]['data_date'],'expected_data_date':expected,
        'status':'date_verified' if expected else 'latest_available_date_not_calendar_verified',
        'funds':funds,'raw_sha256':hashes,'requests_per_successful_collection':5,
        'images_generated':False,'ai_used':False,'published':False}


def export(out,snapshot,*,no_ai=False,retry_ai=False):
    save(out/'数据.json',snapshot)
    fields=['ticker','data_date','futures_barrels','futures_ten_thousand_barrels',
            'swap_market_value_usd','total_barrels','total_barrels_status','daily_change_barrels','nav_usd','net_assets_usd']
    with (out/'数据.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(snapshot['funds'])
    text=['# 原油ETF数据',f"\n官方数据日：{snapshot['data_date']}。抓取时间：{snapshot['fetched_at']}。\n"]
    for fund in snapshot['funds']:
        text.append(f"- {fund['ticker']}：期货桶数 {fund['futures_ten_thousand_barrels']} 万桶；掉期原始美元金额 {fund['swap_market_value_usd']}。")
    text.extend(['\nUSO含掉期，未确认与金十相同的参考价格，暂不输出其合计名义桶数。',
        '首次采集未提供前日持仓，日变化为空，不代表零；份额申赎不替代持仓变化。',
        '日期为发行方持仓数据日，未配置预期交易日时仅标记最新可用，不能冒充当日已更新。',
        '\n来源：https://www.uscfinvestments.com/holdings/uso 与 https://www.uscfinvestments.com/holdings/bno'])
    (out/'数据说明.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    funds=snapshot['funds'];uso,bno=funds
    fmt=lambda value:format(Decimal(value).normalize(),'f')
    title=f"【原油ETF】USO、BNO期货持仓分别为{fmt(uso['futures_ten_thousand_barrels'])}万桶和{fmt(bno['futures_ten_thousand_barrels'])}万桶"
    body=(f"截至{snapshot['data_date']}，USO（WTI）期货持仓为{fmt(uso['futures_ten_thousand_barrels'])}万桶，"
          f"BNO（Brent）期货持仓为{fmt(bno['futures_ten_thousand_barrels'])}万桶。"
          '以上为按标准期货合约规格换算的桶数，不含掉期桶等值，也不代表实物库存。'
          'USO另有掉期持仓，合计名义敞口尚待核对换算口径；当前缺少前日可比持仓，不判断单日增减。')
    rule={'title':title,'body':body,'approved_titles':[title,
          f"【原油ETF】{snapshot['data_date']}USO与BNO期货持仓数据更新"],'approved_bodies':[body]}
    options={'category':'oil_etf','data_date':snapshot['data_date'],'rule':rule,
             'limitations':['USO总名义敞口及两基金日变化尚未完整复现金十口径'],'ready':False}
    write_delivery(out,**options,deepseek={'status':'pending'})
    ai_path=out/'DeepSeek状态.json'
    if no_ai:ai={'status':'disabled'}
    elif ai_path.exists() and not retry_ai:ai=json.loads(ai_path.read_text(encoding='utf-8'))
    else:
        from copy_tracks import generate_deepseek
        ai=generate_deepseek(rule)
    save(ai_path,ai);write_delivery(out,**options,deepseek=ai)


def valid_cache(out,snapshot):
    try:
        for name,digest in snapshot['raw_sha256'].items():
            if hashlib.sha256((out/name).read_bytes()).hexdigest()!=digest:return False
        rebuilt=[normalize(t,(out/f'holding-{t}-full.json').read_bytes(),(out/f'dailyprice-{t}.json').read_bytes(),snapshot['expected_data_date']) for t in FUNDS]
        return rebuilt==snapshot['funds']
    except (OSError,KeyError,DataError):return False


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir',type=Path,default=HERE/'data')
    p.add_argument('--expected-date',help='Required official US holdings date, ISO format; controlled by trading calendar')
    p.add_argument('--refresh',action='store_true')
    p.add_argument('--retries',type=int,default=2)
    p.add_argument('--no-ai',action='store_true')
    p.add_argument('--retry-ai',action='store_true')
    args=p.parse_args(argv);state=args.state_dir.resolve();state.mkdir(parents=True,exist_ok=True)
    try:
        if args.expected_date:date.fromisoformat(args.expected_date)
        if not 0<=args.retries<=3:raise DataError('configuration_error','Retries must be 0..3')
        issue=datetime.now(CHINA).date().isoformat();out=state/'snapshots'/issue/(args.expected_date or 'latest')
        out.mkdir(parents=True,exist_ok=True);snapshot=None
        if (out/'数据.json').exists() and not args.refresh:
            cached=json.loads((out/'数据.json').read_text(encoding='utf-8'))
            if not valid_cache(out,cached):raise DataError('cache_corrupt','Saved source data failed validation')
            snapshot=cached;reused=True
        else:
            reused=False
            # Unique staging folder preserves earlier valid raw files if a refresh fails.
            from uuid import uuid4
            attempt=state/'attempts'/f'{issue}-{uuid4().hex[:10]}';attempt.mkdir(parents=True)
            for retry in range(args.retries+1):
                try:snapshot=fetch(attempt,args.expected_date);break
                except DataError as exc:
                    if exc.code!='network_error' or retry==args.retries:raise
                    time.sleep(min(2**retry,4))
            for name in snapshot['raw_sha256']:(out/name).write_bytes((attempt/name).read_bytes())
        export(out,snapshot,no_ai=args.no_ai,retry_ai=args.retry_ai)
        # The pointer records source dates and readiness explicitly; it never implies publication.
        result={'status':'ok','source_status':snapshot['status'],'data_date':snapshot['data_date'],
                'output_dir':str(out),'reused':reused,'published':False}
        save(state/'latest.json',result);save(state/'status.json',result)
        print(json.dumps(result,ensure_ascii=False));return 0
    except (DataError,ValueError) as exc:
        result={'status':getattr(exc,'code','configuration_error'),'message':str(exc)}
        save(state/'status.json',result);print(json.dumps(result,ensure_ascii=False),file=sys.stderr);return 3
    except Exception as exc:
        result={'status':'failed','error_type':type(exc).__name__}
        save(state/'status.json',result);print(json.dumps(result),file=sys.stderr);return 1


if __name__=='__main__':raise SystemExit(main())
