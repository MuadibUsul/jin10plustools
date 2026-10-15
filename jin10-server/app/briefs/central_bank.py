"""Free PBC public-market announcements. No account or browser required."""
import hashlib
import re
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

INDEX = 'https://www.pbc.gov.cn/zhengcehuobisi/125207/125213/125431/125475/index.html'


class PBCParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.cells=[]; self.text=[]; self.link=None; self.cell=None

    def handle_starttag(self, tag, attrs):
        if tag=='a': self.link=[dict(attrs).get('href',''), []]
        if tag in ('td','th'): self.cell=[]

    def handle_data(self, text):
        self.text.append(text)
        if self.link is not None: self.link[1].append(text)
        if self.cell is not None: self.cell.append(text)

    def handle_endtag(self, tag):
        if tag=='a' and self.link is not None:
            self.links.append((self.link[0], ''.join(self.link[1]))); self.link=None
        if tag in ('td','th') and self.cell is not None:
            self.cells.append(''.join(self.cell).strip()); self.cell=None


def decode(raw):
    for charset in ('utf-8','gb18030'):
        try: return raw.decode(charset)
        except UnicodeError: pass
    raise ValueError('央行公告编码无法识别')


def parse_article(raw, day):
    p=PBCParser(); p.feed(decode(raw)); text=' '.join(p.text)
    d=date.fromisoformat(day)
    if not re.search(rf'{d.year}年\s*{d.month}月\s*{d.day}日',text):
        raise ValueError('央行公告不是所需日期')
    cells=[re.sub(r'\s+','',x) for x in p.cells]
    result=[]
    for i,cell in enumerate(cells):
        if cell=='7天' and i+3<len(cells):
            rate,bid,award=cells[i+1:i+4]
            if not re.fullmatch(r'\d+(?:\.\d+)?%',rate): continue
            if not all(re.fullmatch(r'[\d,]+(?:\.\d+)?亿元',s) for s in (bid,award)): continue
            result.append({'term_days':7,'rate_percent':rate[:-1],
                           'bid_yi':bid[:-2].replace(',',''),'awarded_yi':award[:-2].replace(',','')})
    if len(result)!=1: raise ValueError('未找到唯一7天期逆回购操作表，等待公告或人工核对')
    return {'date':day,**result[0], 'full_demand_met':'全额满足' in text}


def download(url):
    if urlsplit(url).hostname!='www.pbc.gov.cn': raise ValueError('非央行公告地址')
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20) as r:
        raw=r.read(2_000_001)
    if len(raw)>2_000_000: raise ValueError('央行页面过大')
    return raw


def fetch(day, out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    index=download(INDEX);(out/'pbc-index.html').write_bytes(index)
    parser=PBCParser();parser.feed(decode(index))
    # Links are discovered from the official list, never fabricated from dates.
    for href,title in parser.links:
        if '公开市场业务交易公告' not in title:continue
        url=urljoin(INDEX,href)
        if day.replace('-','') not in url:continue
        raw=download(url);(out/'pbc-announcement.html').write_bytes(raw)
        return {**parse_article(raw,day),'url':url,'sha256':hashlib.sha256(raw).hexdigest()}
    raise ValueError('当日7天期逆回购公告尚未发布')
