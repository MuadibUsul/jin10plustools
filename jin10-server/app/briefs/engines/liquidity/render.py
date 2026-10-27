"""Deterministic white-card liquidity poster; no image/model generation."""
from __future__ import annotations

import base64
import hashlib
import html
import io
import json
import re
from functools import lru_cache
from pathlib import Path
import xml.etree.ElementTree as ET
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from app.services.copy_dates import copy_dates
from app.briefs.delivery import default_notes
from app.services.image_delivery import SYSTEM_CJK

HERE=Path(__file__).resolve().parent
TITLE='【流动性指标】质押式回购利率走势与最新行情'
COLORS={'DR001':'#1677FF','DR007':'#13A69B','DR014':'#DC6671'}
NAVY='#18385F';TEXT='#30465E';MUTED='#7B8C9D';BG='#F3F6FA';BORDER='#E7EDF4'
EXPLANATION=tuple(default_notes('liquidity').splitlines())


@lru_cache(maxsize=4)
def coverage(path):
    from fontTools.ttLib import TTFont
    font=TTFont(str(path),fontNumber=0)
    try:return set(font.getBestCmap() or {})
    finally:font.close()


class Poster:
    def __init__(self,height):
        self.width=2160;self.height=height
        self.image=Image.new('RGB',(self.width,height),BG);self.draw=ImageDraw.Draw(self.image)
        self.font_cache={};self.svg=[];self.texts=[]
        self.fallback_path=next((path for path in SYSTEM_CJK if path.is_file() and coverage(path)),None)

    def font(self,size,bold=False):
        key=(size,bold)
        if key not in self.font_cache:
            self.font_cache[key]=ImageFont.truetype(str(HERE/'assets'/('PlusPosterSans-Semibold.ttf' if bold else 'PlusPosterSans-Regular.ttf')),size)
        return self.font_cache[key]

    def glyph_font(self,char,size,bold=False):
        primary_path=HERE/'assets'/('PlusPosterSans-Semibold.ttf' if bold else 'PlusPosterSans-Regular.ttf')
        if ord(char) in coverage(primary_path):return self.font(size,bold)
        if self.fallback_path and ord(char) in coverage(self.fallback_path):
            key=(size,bold,'fallback')
            if key not in self.font_cache:self.font_cache[key]=ImageFont.truetype(str(self.fallback_path),size)
            return self.font_cache[key]
        raise ValueError(f'No available font can render U+{ord(char):04X}')

    def text_width(self,value,size,bold=False):
        return sum(self.glyph_font(char,size,bold).getlength(char) for char in value)

    def rectangle(self,box,fill,radius=0,outline=None):
        self.draw.rounded_rectangle(box,radius=radius,fill=fill,outline=outline,width=2)
        x1,y1,x2,y2=box
        self.svg.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="{radius}" fill="{fill}"'+
                        (f' stroke="{outline}" stroke-width="2"' if outline else '')+'/>')

    def text(self,xy,text,size=44,color=TEXT,bold=False,anchor='lt'):
        width=self.text_width(text,size,bold);x,y=xy
        if anchor=='mt':x-=width/2
        elif anchor=='rt':x-=width
        cursor=x
        baseline=y-self.font(size,bold).getbbox('中',anchor='ls')[1]
        for char in text:
            font=self.glyph_font(char,size,bold)
            self.draw.text((cursor,baseline),char,font=font,fill=color,anchor='ls')
            cursor+=font.getlength(char)
        self.svg.append(f'<text x="{x:.3f}" y="{baseline:.3f}" font-family="PlusPosterSans, Noto Sans CJK SC, sans-serif" '
                        f'font-size="{size}" font-weight="{600 if bold else 400}" fill="{color}">{html.escape(text)}</text>')
        self.texts.append(text)

    def line(self,xy,color=BORDER,width=2):
        self.draw.line(xy,fill=color,width=width)
        self.svg.append(f'<line x1="{xy[0]}" y1="{xy[1]}" x2="{xy[2]}" y2="{xy[3]}" stroke="{color}" stroke-width="{width}"/>')

    def circle(self,x,y,radius,fill):
        self.draw.ellipse((x-radius,y-radius,x+radius,y+radius),fill=fill)
        self.svg.append(f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{fill}"/>')

    def paragraph(self,text,x,y,width,*,size=44,line_height=72,color=TEXT):
        line='';line_width=0
        for char in text:
            char_width=self.glyph_font(char,size).getlength(char)
            if line and line_width+char_width>width:
                self.text((x,y),line,size,color);y+=line_height;line='';line_width=0
            line+=char;line_width+=char_width
        if line:self.text((x,y),line,size,color);y+=line_height
        return y

    def svg_string(self,plot_svg,plot_box,logo_svg=''):
        fonts=[]
        for weight,name in [(400,'Regular'),(600,'Semibold')]:
            encoded=base64.b64encode((HERE/'assets'/f'PlusPosterSans-{name}.ttf').read_bytes()).decode()
            fonts.append(f"@font-face{{font-family:PlusPosterSans;src:url(data:font/ttf;base64,{encoded});font-weight:{weight};}}")
        x,y,w,h=plot_box
        root=ET.fromstring(plot_svg)
        view=root.attrib.get('viewBox');inner=''.join(ET.tostring(e,encoding='unicode') for e in root if e.tag.rsplit('}',1)[-1]!='metadata')
        graph=f'<svg x="{x}" y="{y}" width="{w}" height="{h}" viewBox="{view}">{inner}</svg>'
        # The plot is inserted at its visual location after all white card backgrounds.
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="2160" height="{self.height}" '
                f'viewBox="0 0 2160 {self.height}"><defs><style>{"".join(fonts)}</style></defs>'
                f'<rect width="2160" height="{self.height}" fill="{BG}"/>'+''.join(self.svg)+graph+logo_svg+'</svg>')


def source_y_axis(history):
    """Source ECharts settings: scale=true, splitNumber=3, gap=[0,2%]."""
    values=[Decimal(r[code]) for r in history['rows'] for code in history['series'] if r.get(code) is not None]
    low,high=min(values),max(values)
    if low==high:
        padding=abs(low)/2 if low else Decimal(1)
        low-=padding;high+=padding
    high+=(high-low)*Decimal('.02')
    rough=(high-low)/3
    power=Decimal(10)**rough.adjusted();fraction=rough/power
    nice=Decimal(1) if fraction<Decimal('1.5') else Decimal(2) if fraction<Decimal('2.5') else Decimal(3) if fraction<4 else Decimal(5) if fraction<7 else Decimal(10)
    interval=nice*power
    start=(low/interval).to_integral_value(rounding=ROUND_FLOOR)*interval
    end=(high/interval).to_integral_value(rounding=ROUND_CEILING)*interval
    ticks=[];value=start
    while value<=end:
        ticks.append(float(value));value+=interval
    return float(start),float(end),ticks


def plot_history(history):
    width,height=1872,750
    plt.rcParams.update({'path.simplify':False,'agg.path.chunksize':0,'svg.fonttype':'path'})
    fig,ax=plt.subplots(figsize=(width/150,height/150),dpi=150,facecolor='white')
    fig.subplots_adjust(left=.075,right=.98,top=.90,bottom=.15)
    ax.set_facecolor('white')
    rows=history['rows'];x=np.arange(len(rows));audit={}
    for code in history['series']:
        values=np.array([float(r[code]) if r.get(code) is not None else np.nan for r in rows])
        mask=~np.isnan(values)
        line,=ax.plot(x[mask],values[mask],color=COLORS[code],linewidth=2.1,solid_capstyle='round',label=code)
        np.testing.assert_array_equal(line.get_ydata(),values[mask])
        audit[code]={'source_points':len(rows),'nonmissing_points':int(np.count_nonzero(~np.isnan(values))),
                     'plotted_value_mismatches':0,'minimum':float(np.nanmin(values)),'maximum':float(np.nanmax(values))}
    ax.set_xlim(0,len(rows)-1)
    low,high,tick_values=source_y_axis(history)
    ax.set_ylim(low,high);ax.set_yticks(tick_values)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FormatStrFormatter('%.2f'))
    ticks=sorted(set([0,len(rows)//3,2*len(rows)//3,len(rows)-1]))
    ax.set_xticks(ticks,[rows[i]['date'] for i in ticks])
    ax.get_xticklabels()[0].set_horizontalalignment('left')
    ax.get_xticklabels()[-1].set_horizontalalignment('right')
    ax.tick_params(axis='both',colors='#7B8C9D',labelsize=17,length=0,pad=14)
    ax.grid(axis='y',color='#E5EBF2',linestyle=(0,(4,5)),linewidth=.8)
    for side in ('top','right','left'):ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color('#E7EDF4')
    unit=ax.text(-.005,1.055,'%',transform=ax.transAxes,color='#7B8C9D',fontsize=18,ha='left',va='bottom')
    fig.canvas.draw()
    renderer=fig.canvas.get_renderer()
    for label in ax.get_xticklabels():
        box=label.get_window_extent(renderer)
        assert box.x0>=0 and box.x1<=width and box.y0>=0
    assert unit.get_window_extent(renderer).y1<=height
    for code in audit:
        audit[code]['axis_y_limits']=[float(v) for v in ax.get_ylim()]
        audit[code]['axis_ticks']=tick_values
        audit[code]['connect_nulls_as_source']=True
        audit[code]['date_labels_fully_visible']=True
    png=io.BytesIO();svg=io.StringIO()
    fig.savefig(png,format='png',dpi=150,facecolor='white');fig.savefig(svg,format='svg',facecolor='white');plt.close(fig)
    png.seek(0)
    return Image.open(png).convert('RGB'),svg.getvalue(),audit


def render_poster(snapshot,out_dir,*,logo_path=None,logo_kind='original_user_file',notes=None):
    quote=snapshot['quotes'];history=snapshot['history'];out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    notes=default_notes('liquidity') if notes is None else notes
    paragraphs=notes.strip().splitlines()
    # Size the editable note card from the same font and wrapping used for output.
    explanation_top=1464+320+len(quote['rows'])*126+52
    measuring=Poster(5000);body_end=explanation_top+164
    for paragraph in paragraphs:
        body_end=measuring.paragraph(paragraph,152,body_end,1856,size=44,line_height=68)+26
    explanation_bottom=max(explanation_top+300,body_end+36)
    height=explanation_bottom+210
    poster=Poster(height)
    poster.text((96,96),TITLE,76,NAVY,True)
    curve_names=' / '.join(history['series'])
    extras=[r['product'] for r in quote['rows'] if r['product'] not in history['series']]
    products='涉及品种：'+curve_names+('；行情另含 '+' / '.join(extras) if extras else '')
    poster.text((100,224),products,42,MUTED)
    poster.text((100,290),copy_dates(f"历史：{history['start']} — {history['end']}"),38,MUTED)
    # History card.
    poster.rectangle((80,396,2080,1412),'white',40,BORDER)
    poster.text((136,450),'历史走势',60,NAVY,True)
    for i,code in enumerate(history['series']):
        x=138+i*330
        poster.line((x,575,x+76,575),COLORS[code],8)
        poster.text((x+98,551),code,42,TEXT)
    plot,plot_svg,plot_audit=plot_history(history)
    plot_box=(136,624,plot.width,plot.height)
    poster.image.paste(plot,(plot_box[0],plot_box[1]))
    # Latest quotation card. Values are copied as original source strings.
    table_top=1464;table_bottom=table_top+320+len(quote['rows'])*126
    poster.rectangle((80,table_top,2080,table_bottom),'white',40,BORDER)
    poster.text((136,table_top+54),'前一交易日收盘行情',60,NAVY,True)
    poster.text((2024,table_top+72),'行情时间 '+copy_dates(quote['updated_at']),34,MUTED,anchor='rt')
    header_y=table_top+174
    poster.rectangle((128,header_y,2032,header_y+116),'#F0F6FD',20)
    centers=[340,800,1260,1750]
    headers=['品种','加权利率（%）','最新利率（%）','平均回购期限（天）']
    for x,text in zip(centers,headers):poster.text((x,header_y+36),text,34,NAVY,True,'mt')
    values_audit=[]
    for i,row in enumerate(quote['rows']):
        y=header_y+142+i*126
        poster.text((168,y+16),row['product'],48,NAVY,True)
        for x,key in zip(centers[1:4],('weighted_rate','latest_rate','average_term_days')):
            display=row[key] if row[key] is not None else '—'
            poster.text((x,y+12),display,52,'#1677FF' if key=='weighted_rate' else TEXT,key=='weighted_rate','mt')
            values_audit.append({'product':row['product'],'field':key,'source':row[key],'display':display})
        if i<len(quote['rows'])-1:poster.line((136,y+103,2024,y+103),BORDER,2)
    # Editable explanation is drawn into the image of this immutable edition.
    poster.rectangle((80,explanation_top,2080,explanation_bottom),'#F2F9FF',28,'#DDEDF8')
    cx,cy=174,explanation_top+86
    poster.circle(cx,cy,43,'#1677FF');poster.circle(cx,cy-3,13,'white')
    poster.rectangle((cx-7,cy+6,cx+7,cy+23),'white',3)
    for dx,dy in ((0,-27),(-23,-16),(23,-16),(-27,7),(27,7)):
        poster.line((cx+dx*.78,cy+dy*.78,cx+dx,cy+dy),'white',3)
    poster.text((252,explanation_top+52),'指标解读',60,NAVY,True)
    y=explanation_top+164
    for paragraph in paragraphs:
        y=poster.paragraph(paragraph,152,y,1856,size=44,line_height=68)+26
    footer_y=explanation_bottom+78
    poster.text((104,footer_y),'数据来源：中国货币网',34,MUTED)
    poster.text((104,footer_y+50),'利率单位：%   平均回购期限单位：天',30,MUTED)
    logo_audit={'status':'missing_original_logo','source_kind':None};logo_svg=''
    if logo_path:
        logo_path=Path(logo_path);original=Image.open(logo_path).convert('RGBA')
        # Scale proportionally only. No recoloring, redrawing, tracing or imitation.
        max_width,max_height=410,98
        scale=min(max_width/original.width,max_height/original.height,1.0)
        size=(max(1,round(original.width*scale)),max(1,round(original.height*scale)))
        logo=original.resize(size,Image.Resampling.LANCZOS)
        x=2048-size[0];y=footer_y+15
        poster.image.paste(logo,(x,y),logo)
        encoded=base64.b64encode(logo_path.read_bytes()).decode()
        mime='image/png' if logo_path.suffix.lower()=='.png' else 'image/jpeg'
        logo_svg=f'<image x="{x}" y="{y}" width="{size[0]}" height="{size[1]}" preserveAspectRatio="xMidYMid meet" href="data:{mime};base64,{encoded}"/>'
        logo_audit={'status':'original_asset_used','source_kind':logo_kind,'sha256':hashlib.sha256(logo_path.read_bytes()).hexdigest(),
                    'original_size':list(original.size),'output_size':list(size),'proportional_scale_only':True,'recolored':False,'box':[x,y,x+size[0],y+size[1]]}
    day=quote['quote_date'].replace('-','')
    png=out/f'流动性指标_{day}_Plus简约版.png';svg_path=png.with_suffix('.svg')
    poster.image.save(png,dpi=(200,200));svg_path.write_text(poster.svg_string(plot_svg,plot_box,logo_svg),encoding='utf-8')
    audit={'title':TITLE,'size':list(poster.image.size),'source_range':[history['start'],history['end']],
           'quote_updated_at':quote['updated_at'],'chart_series':history['series'],
           'quote_time_basis':'ChinaMoney overall quote timestamp; not fetch time or individual instrument update time',
           'table_columns':headers,'explanation_text':notes,
           'quotation_products':[r['product'] for r in quote['rows']],
           'curves':plot_audit,'table_cells':values_audit,
           'policy_rate_supplied':False,'current_liquidity_conclusion':False,
           'website_watermark_included':False,'font':'bundled PlusPosterSans (OFL Noto-derived subset)',
           'logo':logo_audit,'release_ready':logo_path is not None,'code_only':True,
           'svg_contains_vector_chart_and_text':True}
    return png,svg_path,audit
