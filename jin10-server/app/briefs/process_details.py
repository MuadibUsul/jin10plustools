"""Readable production evidence, shared by every brief product.

Adapters may supply ``process_details`` (sources/calculations/inputs) in their
payload. The API and UI do not need product-specific changes for new adapters.
Never return engine stdout, request headers, credentials or arbitrary files.
"""
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.services.copy_dates import copy_dates


def public_url(value):
    try:
        parsed = urlsplit(str(value))
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            return None
        # Only public source locations, never signed query strings or fragments.
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))
    except ValueError:
        return None


def safe_value(value, depth=0):
    if depth > 6:
        return '…'
    if isinstance(value, dict):
        return {str(k)[:100]: safe_value(v, depth+1) for k, v in list(value.items())[:45]
                if not any(word in str(k).lower() for word in ('key', 'token', 'secret', 'password', 'authorization', 'cookie', 'header'))}
    if isinstance(value, list):
        return [safe_value(v, depth+1) for v in value[:25]]
    if isinstance(value, str):
        return public_url(value) if value.startswith(('http://', 'https://')) else copy_dates(value[:4000])
    return value if isinstance(value, (int, float, bool)) or value is None else str(value)[:500]


def read_json(path):
    try:
        if path.is_symlink() or path.stat().st_size > 8_000_000:
            return None
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return None


def describe(category, payload):
    """Explain actual adapter formulas and substitute values from its result."""
    facts = payload.get('facts') or {}
    sources, calculations, inputs = [], [], []

    def source(label, url):
        url = public_url(url)
        if url and url not in [s['url'] for s in sources]:
            sources.append({'label': label, 'url': url})

    def value(label, raw, unit=''):
        if raw is not None:
            inputs.append({'label': label, 'value': copy_dates(str(raw)), 'unit': unit})

    def number(raw, digits=2):
        return f'{Decimal(str(raw)):.{digits}f}'

    try:
        if category == 'volume':
            source('金十数据 · 沪深成交额', facts.get('source'))
            value('沪深合计成交额', facts.get('amount_yi'), '亿元')
            value('20日成交额均线', facts.get('ma20_yi'), '亿元')
            value('对比交易日', facts.get('previous_date'))
            calculations = ['逐日将沪市成交额与深市成交额相加；单位统一为亿元。',
                '20日均线 = 最近20条有效交易日合计成交额之和 ÷ 20；不足20条不计算均线。',
                '日变动 = 当日合计 − 前一有效交易日合计；变动率 = 日变动 ÷ 前一日合计 × 100%。',
                '从最新交易日向前统计连续低于或不低于20日均线的天数；标题按成交额 ÷ 10000 转成万亿元。']
            if facts.get('amount_yi') is not None:
                calculations.append(f"本期成交额：{facts['amount_yi']} ÷ 10000 = {number(Decimal(str(facts['amount_yi']))/10000)} 万亿元。")
        elif category == 'turnover':
            source('乐咕乐股 · 市场换手率统计', 'https://legulegu.com/stockdata/market-turn-over-ratio-statistics')
            for key, label in [('low_ma9','低于3%占比的9日均线'), ('high_ma9','高换手占比的9日均线'), ('previous_low_ma9','前日低换手均线'), ('previous_high_ma9','前日高换手均线')]:
                value(label, facts.get(key), '%')
            calculations = ['低换手日占比 = belowPercent3（换手率低于3%的公司数）÷ totalCompany（公司总数）× 100%。',
                '高换手日占比 =（totalCompany − belowPercent5）÷ totalCompany × 100%；沿用来源的5%分组边界。',
                '分别取最近9条有效交易日的日占比作简单平均，四舍五入保留两位小数，得到两条9日均线。',
                '与前一条有效交易日的9日均线比较；高换手均线与17%参考线比较，低换手图保留96%参考线；按均线升降组合生成描述。']
        elif category == 'valuation':
            latest, previous = facts.get('erp_latest', {}), facts.get('erp_previous', {})
            source('东方财富 · 全A上市公司总市值', 'https://datacenter-web.eastmoney.com/api/data/v1/get')
            source('雪球基金 · 沪深300估值数据', 'https://danjuanfunds.com/djapi/index_eva/pe_history/SH000300')
            source('中债 · 国债收益率曲线', 'https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery')
            source('A股总市值来源', facts.get('market_cap', {}).get('source'))
            source('PE来源', latest.get('pe_source'))
            source('指数收盘来源', latest.get('close_source'))
            source('国债收益率来源', latest.get('bond_source'))
            gdp = facts.get('gdp', {})
            source('国家统计局 · 上年度GDP', gdp.get('source'))
            for row in facts.get('market_cap', {}).get('rows', []):
                if row.get('date') == payload.get('data_date'):
                    value('本期A股总市值', row.get('market_cap_yi'), '亿元')
            value('上年度GDP', gdp.get('value_yi'), '亿元')
            value('市值加权滚动PE（计算输入）', latest.get('pe_ttm_mcw'), '倍')
            value('国债收益率', latest.get('bond_yield_number'), '%')
            value('本期ERP', latest.get('erp_percent'), '%')
            value('前一期ERP', previous.get('erp_percent'), '%')
            value('巴菲特指数', facts.get('buffett_percent'), '%')
            calculations = ['巴菲特指数 = A股总市值 ÷ 上年度全年GDP；正文乘100显示百分比，图中保留比值刻度。',
                'PE原始值先按既定口径四舍五入到两位小数。ERP（%）= 100 ÷ 市值加权滚动PE − 国债收益率（%）。',
                '本期ERP减去前一交易日ERP得到百分点变动；与6%参考线比较。缺少任一必要交易日时停止生成，不用旧值冒充当天。']
            if latest.get('pe_ttm_mcw') and latest.get('bond_yield_number') is not None:
                calculations.append(f"本期代入：100 ÷ {latest['pe_ttm_mcw']} − {latest['bond_yield_number']} = {number(latest['erp_percent'])}%。")
        elif category == 'liquidity':
            source('中国货币网 · 质押式回购行情', 'https://www.chinamoney.com.cn/chinese/mkdatapm/')
            quotes, bank = facts.get('quotes', {}), facts.get('central_bank')
            value('来源行情更新时间', quotes.get('updated_at'))
            for row in quotes.get('rows', []):
                value(row.get('product', 'DR')+'加权利率', row.get('weighted_rate'), '%')
            calculations = ['历史走势读取DR001、DR007、DR014历史序列；最新行情表读取来源的加权利率、最新利率和平均回购期限，不自行伪造盘中行情。',
                '行情更新时间来自中国货币网 showDateCN 字段，与本程序抓取时间分别记录；抓取发生在现在，不代表来源刚刚更新。']
            if bank:
                source('央行当日操作公告', bank.get('source_url') or bank.get('url'))
                value('7天期逆回购中标利率', bank.get('rate_percent'), '%')
                dr = next((r for r in quotes.get('rows', []) if r.get('product') == 'DR007'), {})
                if dr.get('weighted_rate') is not None and bank.get('rate_percent') is not None:
                    spread = (Decimal(str(dr['weighted_rate']))-Decimal(str(bank['rate_percent'])))*100
                    calculations.append(f"DR007与政策利率之差 =（{dr['weighted_rate']} − {bank['rate_percent']}）× 100 = {spread:.2f} 个基点。")
            else:
                calculations.append('本次未取得对应日期央行操作公告，仅描述DR行情，不推算政策利率。')
        elif category == 'oil_etf':
            for fund in facts.get('official_source', {}).get('funds', []):
                source(fund.get('ticker', '')+'发行方持仓', fund.get('source_page'))
            for fund in facts.get('funds', []):
                ticker = fund['ticker']
                for key, label, unit in [('futures_barrels','期货持仓','桶'), ('swap_barrels_estimate','掉期桶等值估算','桶'), ('barrels','合计名义敞口','桶'), ('change_barrels','期货日变动','桶')]:
                    value(ticker+label, fund.get(key), unit)
            calculations = ['筛选USO的WTI原油期货与BNO的Brent原油期货；期货桶数 = 合约数量 × 每张合约1000桶，再逐笔加总。',
                '掉期桶等值估算 = 掉期市值（美元）÷ 当期首个期货合约参考价格（美元/桶）；合计名义敞口 = 期货桶数 + 掉期桶等值估算。',
                '日变化只比较当日与前一工作日期货桶数，不包含掉期变化；缺少前日证据时不判断单日增减。正文桶数 ÷ 10000 后显示万桶。']
        elif category == 'us_barometer':
            source('美股总市值（Wilshire 5000）', facts.get('sources', {}).get('wilshire'))
            source('美国季度GDP（FRED，年化）', facts.get('sources', {}).get('gdp'))
            source('标普500股权风险溢价（StreetStats）', facts.get('sources', {}).get('erp'))
            value('巴菲特指数', facts.get('buffett_pct'), '%')
            value('股权风险溢价（ERP）', facts.get('erp_pct'), '%')
            value('采用季度GDP', facts.get('gdp_period'))
            value('GDP（十亿美元，年化）', facts.get('gdp_value'))
            calculations = ['巴菲特指数 = 美股总市值 ÷ 最近已发布季度GDP × 100%；Wilshire 5000 为全市值指数，点位与十亿美元市值一一对应。',
                'ERP 直接采用来源的逐日数值（标普500盈利收益率 − 10年期美债收益率），不二次拟合或插值。',
                '分级：巴菲特指数 70%以下低估、70-100%合理、100-150%偏高、150-200%高估、200%以上严重高估；ERP 7%以上很高、6-7%偏高、5.5-6%中性、4.5-5.5%偏低、4.5%以下很低。',
                '综合结论 = 两项分级得分之和：≥4 高、2-3 较高、-1 到 1 中性、-3 到 -2 较低、≤-4 低。',
                f"本期：巴菲特指数 {facts.get('buffett_pct')}%（{facts.get('buffett_grade')}），ERP {facts.get('erp_pct')}%（{facts.get('erp_grade')}），结论{facts.get('verdict')}。"]
        elif category == 'us_structure':
            source('标普500等权指数（^SPXEW）', 'https://query1.finance.yahoo.com/v8/finance/chart/%5ESPXEW')
            source('标普500 / 纳指 / 道指 / 罗素2000（Yahoo Finance）', 'https://query1.finance.yahoo.com/v8/finance/chart/%5EGSPC')
            value('标普500涨跌幅', facts.get('spx_change'), '%')
            value('纳斯达克涨跌幅', facts.get('ndx_change'), '%')
            value('道指涨跌幅', facts.get('dji_change'), '%')
            value('罗素2000涨跌幅', facts.get('rut_change'), '%')
            value('等权−市值加权差值（点）', facts.get('gap'))
            value('差值近一年低位（点）', facts.get('gap_low'))
            value('低位日期', facts.get('gap_low_date'))
            value('差值一年百分位', facts.get('gap_percentile'))
            value('罗素2000距近十年前高回撤', facts.get('rut_drawdown'), '%')
            calculations = ['等权−市值加权差值 = 标普500等权指数 − 标普500指数，单位为指数点；差值扩大表示上涨扩散到更多公司，收窄表示资金向权重股集中。',
                '差值百分位 =（当日差值 − 近一年最低）÷（近一年最高 − 近一年最低），用于判断市场宽度在年内的位置。',
                '罗素2000回撤 = 当日收盘 ÷ 近十年前高 − 1；新低判断比较当日最低点与此前每日最低点，不补造盘中数据。',
                '标题与结论按差值百分位分级：低于35%为“依然不佳”，35%-65%为“中性”，高于65%为“有所改善”。',
                f"本期：标普500 {facts.get('spx_change')}%、纳指 {facts.get('ndx_change')}%、道指 {facts.get('dji_change')}%、罗素2000 {facts.get('rut_change')}%；等权差 {facts.get('gap')} 点（百分位 {facts.get('gap_percentile')}），结论{facts.get('verdict')}。"]
    except (KeyError, TypeError, ValueError, InvalidOperation, ZeroDivisionError):
        calculations.append('部分本期字段不完整，以下保留已取得的数据供核对；不补造缺失数值。')

    custom = payload.get('process_details') or {}
    for item in custom.get('sources', []):
        source(item.get('label', '数据来源'), item.get('url'))
    calculations.extend(str(s)[:2000] for s in custom.get('calculations', [])[:30])
    inputs.extend(custom.get('inputs', [])[:40])
    return {'sources': sources, 'calculations': calculations, 'inputs': inputs,
            'facts': safe_value(facts)}


def for_job(job, root):
    root = Path(root).resolve()
    folder = root/'runs'/str(job.id)
    payload = read_json(folder/'result.json') or {}
    artifact = (root/str(payload.get('artifact_dir', ''))).resolve()
    if not artifact.is_relative_to(root):
        artifact = folder
    data = describe(job.category, payload)
    evidence = []
    # Fixed, registered snapshot names only; no user-provided paths or directory walk.
    for name in ('volume-raw.json', 'plot-data.json', 'source.json', 'liquidity-snapshot.json',
                 'oil-source.json', 'oil-previous-source.json', 'buffett-plot-data.json', 'erp-plot-data.json'):
        path = folder/name
        if not path.is_file():
            path = artifact/name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 8_000_000:
            continue
        raw = path.read_bytes()
        parsed = read_json(path)
        evidence.append({'name': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                         'rows': len(parsed) if isinstance(parsed, list) else None})
        # A small raw sample supports checking counts and the input to moving averages.
        if isinstance(parsed, list) and name in ('source.json', 'plot-data.json', 'buffett-plot-data.json', 'erp-plot-data.json'):
            data.setdefault('samples', []).append({'name': name, 'rows': safe_value(parsed[-2:])})
        if job.category == 'turnover' and name == 'source.json' and isinstance(parsed, list) and len(parsed) >= 9:
            try:
                window = parsed[-9:]
                low = sum(Decimal(str(r['belowPercent3']))/Decimal(str(r['totalCompany']))*100 for r in window)/9
                high = sum((Decimal(str(r['totalCompany']))-Decimal(str(r['belowPercent5'])))/Decimal(str(r['totalCompany']))*100 for r in window)/9
                data['calculations'].append(f"本期9条日占比均值：低换手 {low:.2f}%，高换手 {high:.2f}%；窗口为 {copy_dates(window[0]['date'])} 至 {copy_dates(window[-1]['date'])}。")
            except (KeyError, TypeError, ValueError, InvalidOperation, ZeroDivisionError):
                pass
        if job.category == 'volume' and name == 'volume-raw.json' and isinstance(parsed, dict):
            rows = parsed.get('data') or []
            row = next((r for r in rows if r.get('date') == payload.get('data_date')), None)
            if row:
                for key, label in [('sh_turn','沪市成交额'), ('sz_turn','深市成交额')]:
                    data['inputs'].append({'label': label,'value': str(row.get(key)), 'unit': '亿元'})
        if name == 'liquidity-snapshot.json' and isinstance(parsed, dict):
            data['fetched_at'] = parsed.get('fetched_at')
    actions = {'produce': '生成稿件', 'refresh': '重新取数', 'render': '从快照重新制图', 'ai': '补跑AI文案'}
    reused = bool(payload and artifact != folder.resolve())
    ai = payload.get('deepseek') or {}
    return {**data, 'evidence': evidence, 'action': actions.get(job.action, job.action),
            'reused': reused, 'available': bool(payload),
            'result': {'title': copy_dates(payload.get('title', '')), 'body': copy_dates(payload.get('dynamic_body', '')),
                       'images': payload.get('images', []), 'warnings': safe_value(payload.get('warnings', [])),
                       'ai_status': ai.get('status', 'not_configured'), 'ai_model': ai.get('model', '')},
            'assembly': ['先以已校验指标生成规则标题与事实正文。',
                         '图表读取同一批已校验数据；本期固定注释按栏目规则放入正文或估值图片。',
                         '启用DeepSeek时，把规则事实交给模型生成候选表达并校验；规则稿保留，AI候选由编辑决定是否采用。',
                         '保存本期版本和来源快照；下载图片时统一加原始水印并检查266000字节上限。']}
