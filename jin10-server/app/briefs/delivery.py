"""Shared delivery contract, copied verbatim into standalone job packages."""
import json
import re
from pathlib import Path

FIXED_NOTES={
    'volume':'注：两市成交量反映了各大机构、游资、广大散户对A股看法，高于均线代表总成交量升高，市场情绪偏乐观，低于均线代表总成交量下降，市场情绪降温。',
    'turnover':'注：1、当换手率低于3%的股票占比上升，尤其是接近历史高位附近（图中虚线，图为反转走势）时，意味着市场很有可能遇到一个阶段低点，容易产生反转或反弹。\n2、当高换手率高于5%的股票占市场总数比例保持在阈值（图中虚线）上方，市场才会出现强的赚钱效应，使得行情具有持续性。牛市的时候，该指标会持续保持在阈值上方，市场迎来普涨行情；而熊市的时候，该指标大多时候在阈值下方，偶尔超过阈值反而要小心冲高回落的风险。',
    'valuation':'注：1、巴菲特指数是通过将股市总市值和GDP来比较，判断目前股市是否高估，通常70-100%为正常估值，低于该区间为低估，高于该区间为高估。\n2、沪深300指数风险溢价（ERP）主要对比沪深300收益和国债收益率，统计2016年以来数据可以发现该指标和股指有明显反向关系，每次股市都处于底部区域时，风险溢价均超过6%，也就是当ERP≥6%，股市有投资性价比，而当ERP≤4%时，则股市往往接近或处于阶段性高点，投资性价比低甚至要小心回调风险。',
    'liquidity':'注：短期货币市场利率如DR007，反映的是市场资金充裕程度。除了实体经济贷款利率，股市、债市等金融市场很多交易本质上是靠“贷款”加杠杆，贷款成本就取决于短期资金利率。DR007通常被视为中国短端利率锚。DR007与央行7天逆回购利率（政策利率）之间的差距就可以看出市场是否缺钱，DR007利率高于政策利率很多，说明市场缺钱，央行可能会出手投放流动性；反之说明市场资金非常宽松。',
    'us_structure':'注：如果标普500等权重与标普500市值加权指数之差减少，说明市场资金集中度上升，科技巨头（比如美股七姐妹）吸收了大量资金，差值若变为负值说明资金过分集中，需要警惕崩盘风险。如果指数之差扩大，说明市场上涨动能可能正在向更多公司扩散，市场有望走向普涨。',
    'us_barometer':'注：1、巴菲特指数是通过将股市总市值和GDP来比较，判断目前股市是否高估，通常70-100%为正常估值，低于该区间为低估，高于该区间为高估。不过该数据具有一定滞后性。\n2、ERP是将股市整体收益率与国债收益对比，来判断股市是否具有投资价值。ERP越高股市越有投资价值，反之则是债市更有吸引力。如果ERP > 7%说明股票比国债便宜得多，通常是大底部或绝佳的买入机会。ERP＜4.5%说明股票明显比国债昂贵，买股票承担了高风险却拿不到应有的高回报。',
}
# Chart notes are the newest sample blocks, verbatim. They are not rewritten
# and they are not appended to the draft body.
FIXED_NOTES['token_cost']=(
    '1、各大模型降价以及用户从昂贵的前沿模型转向更便宜的开源模型、小模型，推动token价格下跌。'
    '\n2、当前阶段价格下降可能刺激需求增加，这有利于AI应用等板块，对云厂商影响复杂，而对OpenAI、Anthropic等模型厂商来说，定价权下降则是个风险因素。'
    '\n3、数据来自OpenAI、Anthropic、DeepSeek等全球顶尖AI模型。')
FIXED_NOTES['gpu_rental']=(
    '1、GPU租赁价格指数主要统计每天云服务商、数据中心和私人租赁市场中英伟达A100、H100、B200等GPU的实际租赁报价。'
    '\n2、A100、H100、B200是英伟达从老到新三代芯片产品（GPU），通常代际越新的GPU性能越好、越能适应大规模复杂大模型，租赁价格也往往越高。'
    '\n3、由于GPU价格太高、更新太快等原因，不少企业选择向云服务商、数据中心等租赁芯片，以节省资金投入。相关指数上涨通常意味着GPU更紧缺、需求更旺；下降则说明供应增加或需求降温。')

OIL_CLOSING='点击查看完整持仓数据，捕捉油价之外的资金变化。'
ALIASES={'01_成交额':'volume','02_换手率':'turnover','03_估值指标':'valuation',
         '04_流动性':'liquidity','05_原油ETF':'oil_etf','valuation_erp':'valuation'}


def default_notes(category):
    return FIXED_NOTES.get(category, OIL_CLOSING if category=='oil_etf' else '')


def complete_body(category,body,notes=None):
    """Append verbatim user-supplied evergreen text, once, after dynamic facts.

    Valuation, turnover, the US barometer, Token CPI and GPU rental carry notes on
    the chart. Their draft body keeps only the dynamic facts.
    """
    category=ALIASES.get(category,category);body=body.strip()
    note=default_notes(category) if notes is None else notes
    if category in ('valuation','turnover','us_barometer'):
        return re.split(r'\n\s*-{3,}\s*\n(?=\s*注：)',body,maxsplit=1)[0].replace(default_notes(category),'').strip()
    if category in ('token_cost','gpu_rental'):
        body=re.split(r'\n\s*-{3,}\s*\n',body,maxsplit=1)[0]
        for line in (note or '').splitlines():
            line=line.strip()
            if line:
                body=body.replace(line,'')
        return body.strip()
    if category=='oil_etf':
        # The closing line continues the dynamic sentence instead of starting a new paragraph.
        if not note:return body
        body=body.replace(note,'').rstrip()
        return body+note if body else note
    if note:
        # Replace any earlier edition of a fixed explanatory block, not daily facts.
        body=re.split(r'\n\s*-{3,}\s*\n(?=\s*注：)',body,maxsplit=1)[0].strip()
        if note in body:body=body.replace(note,'').rstrip().rstrip('-').rstrip()
        return body+'\n\n--------\n\n'+note
    return body


def split_markdown(text):
    lines=text.strip().splitlines()
    if not lines or not lines[0].startswith('# '):raise ValueError('Article must have a title')
    return {'title':lines[0][2:].strip(),'body':'\n'.join(lines[1:]).strip()}


def write_delivery(out,*,category,data_date,rule,images=(),image_required=False,
                   deepseek=None,limitations=(),ready=True):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    title=rule.get('title','').strip();body=rule.get('body','').strip()
    if not title or not body or '\n' in title:raise ValueError('Nonempty single-line title and body are mandatory')
    body=complete_body(category,body)
    assets=[]
    for file in images:
        path=Path(file)
        if not path.is_absolute():path=out/path
        if not path.is_file():raise ValueError('Required image is missing')
        # Each standalone delivery directory carries its own assets.
        if path.parent.resolve()!=out.resolve():raise ValueError('Images must belong to the delivery directory')
        assets.append(path.name)
    if image_required and not assets:raise ValueError('This category requires an image')
    (out/'标题.txt').write_text(title+'\n',encoding='utf-8')
    (out/'正文.txt').write_text(body+'\n',encoding='utf-8')
    (out/'发布稿.md').write_text(f'# {title}\n\n{body}\n',encoding='utf-8')
    ai=deepseek or {'status':'not_configured'}
    tracks={'rules':{'status':'ok','title':title,'body':body,'images':assets},
            'deepseek':{'status':ai.get('status','not_configured')}}
    if ai.get('status')=='ok':
        if not ai.get('title','').strip() or not ai.get('body','').strip():raise ValueError('AI title/body missing')
        ai_body=complete_body(category,ai['body'])
        tracks['deepseek'].update(title=ai['title'],body=ai_body,images=assets)
        (out/'标题_DeepSeek.txt').write_text(ai['title']+'\n',encoding='utf-8')
        (out/'正文_DeepSeek.txt').write_text(ai_body+'\n',encoding='utf-8')
    else:
        # A failed retry must not leave an old AI article looking current.
        for name in ('标题_DeepSeek.txt','正文_DeepSeek.txt'):
            (out/name).unlink(missing_ok=True)
    record={'schema_version':'1.0','category':category,'data_date':data_date,
        'title':title,'body':body,'images':assets,'image_required':image_required,
        'tracks':tracks,'limitations':list(limitations),'ready':bool(ready),'published':False}
    (out/'交付清单.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    return record
