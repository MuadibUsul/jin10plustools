"""Code-only charts; native templates remain in the engines package."""
from pathlib import Path
from datetime import date
import math
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from PIL import Image, ImageDraw, ImageFont
from app.services.copy_dates import copy_dates

FONT=Path(__file__).parent/'assets/PlusBriefs.ttf'
GLYPH_FALLBACK=Path(__file__).parent/'assets/PlusGlyphFallback.ttf'


def font(size=12):return FontProperties(fname=str(FONT),size=size)


def volume_chart(rows,out):
    rows=rows[-75:];x=list(range(len(rows)));last=rows[-1]
    fig=plt.figure(figsize=(12,14.6),dpi=100,facecolor='white')
    ink='#17233B';muted='#7D8798';blue='#5476DC';green='#27A86C'
    def text(x,y,s,size=15,color=ink):fig.text(x,y,s,fontproperties=font(size),color=color)
    text(.07,.96,'PLUS 快讯 / A股温度计',15,muted);text(.07,.905,'沪深两市成交额',32)
    text(.07,.81,f"{float(last['turn'])/10000:.2f}",82,blue);text(.36,.82,'万亿元',23)
    text(.07,.77,f"数据日：{copy_dates(last['date'])}    20日均线：{float(last['ma20'])/10000:.2f}万亿元",16,muted)
    ax=fig.add_axes([.1,.34,.82,.34]);av=fig.add_axes([.1,.16,.82,.12],sharex=ax)
    ax.bar(x,[float(r['turn']) for r in rows],color='#F3BA4B',width=.68)
    ax.plot(x,[float(r['ma20']) for r in rows],color=green,lw=2.5,label='20日均线')
    ax.set_title('成交额（亿元）与20日均线',fontproperties=font(16),loc='left',pad=22)
    ax.legend(prop=font(12),frameon=False);ax.tick_params(axis='x',labelbottom=False)
    av.bar(x,[float(r['vol']) for r in rows],color=blue,width=.68)
    av.set_title('成交量（亿股）',fontproperties=font(15),loc='left',pad=15)
    ticks=sorted(set([0,len(rows)-1]+list(range(10,len(rows)-5,10))))
    av.set_xticks(ticks,[rows[i]['date'][5:].replace('-','/') for i in ticks])
    for a in (ax,av):
        a.set_axisbelow(True);a.grid(axis='y',color='#E9EDF3');a.tick_params(colors=muted,length=0,pad=9)
        for spine in a.spines.values():spine.set_visible(False)
    text(.07,.08,'数据来源：金十数据 · 沪深两市，不含北交所',13,muted)
    text(.07,.05,'20日均线包含当日，按最近20条有效日数据计算。',13,muted)
    path=Path(out)/'成交额.png';fig.savefig(path,dpi=100);plt.close(fig);return path


def buffett_chart(rows,out):
    end=date.fromisoformat(rows[-1]['date'])
    try:start=end.replace(year=end.year-10)
    except ValueError:start=end.replace(year=end.year-10,day=28)
    rows=[r for r in rows if start.isoformat()<=r['date']<=end.isoformat()]
    x=list(range(len(rows)))
    fig,ax=plt.subplots(figsize=(14.08,5.95),dpi=100,facecolor='black')
    fig.subplots_adjust(left=.07,right=.92,top=.81,bottom=.17);ax.set_facecolor('black')
    other=ax.twinx();ax.plot(x,[float(r['close']) for r in rows],color='#A5A5A5',lw=1.4,label='沪深300')
    other.plot(x,[float(r['marketCap'])/float(r['gdp'])*100 for r in rows],color='#22A1D6',lw=1.4,label='A股总市值/GDP')
    for value in (70,100):other.axhline(value,color='#FFC000',lw=1,ls='--')
    ax.set_ylim(0,6000);other.set_ylim(0,160)
    ax.set_title('巴菲特指数（A股总市值/上年度全年GDP）',fontproperties=font(19),color='white',loc='center',pad=26)
    ticks=[round((len(rows)-1)*i/7) for i in range(8)]
    ax.set_xticks(ticks,[rows[i]['date'] for i in ticks]);ax.grid(color='#333333',axis='y',ls='--')
    for a in (ax,other):
        a.tick_params(colors='#B6B6B6',labelsize=10)
        for spine in a.spines.values():spine.set_color('#666666')
    other.set_ylabel('%',color='#B6B6B6')
    ax.legend(prop=font(),labelcolor='white',loc='upper left',frameon=False)
    other.legend(prop=font(),labelcolor='white',loc='upper right',frameon=False)
    path=Path(out)/'巴菲特指数.png';fig.savefig(path,dpi=100);plt.close(fig);return path


VALUATION_PLOT_BOUNDS=((80,105,1324,493),(80,700,1324,1088))


def erp_index_limits(close):
    """Fit the ERP panel's CSI 300 axis to the observed range.

    The index has never traded near zero, so a fixed 0 bottom wastes the lower band.
    Keep 500-point ticks, a round bottom with >=250 points of headroom, and the
    approved 6,000 floor for the top.
    """
    low=max(0,math.floor((min(close)-250)/500)*500)
    high=max(6000,math.ceil(max(close)/500)*500)
    return low,high


def valuation_chart(buffett_rows,erp_rows,out):
    """Draw both valuation panels on one canvas with identical plot geometry."""
    end=date.fromisoformat(erp_rows[-1]['date'])
    try:start=end.replace(year=end.year-10)
    except ValueError:start=end.replace(year=end.year-10,day=28)
    buffett_rows=[r for r in buffett_rows if start.isoformat()<=r['date']<=end.isoformat()]
    erp_rows=[r for r in erp_rows if start.isoformat()<=r['date']<=end.isoformat()]
    if len(buffett_rows)<2 or len(erp_rows)<2:raise ValueError('估值合成图缺少有效历史数据')
    fig=plt.figure(figsize=(14.08,11.90),dpi=100,facecolor='black')
    ticks=[start.toordinal()+round((end.toordinal()-start.toordinal())*i/7) for i in range(8)]
    labels=[date.fromordinal(x).strftime('%Y-%m-%d') for x in ticks]
    ink='#B6B6B6';gray='#A5A5A5';blue='#22A1D6'
    ratio=[float(r['marketCap'])/float(r['gdp']) for r in buffett_rows]
    top_limits=(min(.5,math.floor(min(ratio)*10)/10),max(1.3,math.ceil(max(ratio)*10)/10))
    top_ticks=[round(top_limits[0]+i*.1,1) for i in range(round((top_limits[1]-top_limits[0])*10)+1)]
    close=[float(r['close']) for r in buffett_rows]
    top_left=(min(2500,math.floor(min(close)/500)*500),max(6000,math.ceil(max(close)/500)*500))
    top_left_ticks=list(range(top_left[0],top_left[1]+1,500))
    erp_close=[float(r['close']) for r in erp_rows]
    erp_left=erp_index_limits(erp_close)
    erp_left_ticks=list(range(erp_left[0],erp_left[1]+1,500))
    panels=(
        (buffett_rows,ratio,top_left,top_left_ticks,top_limits,top_ticks,lambda value:f'{value:.1f}',
         ((1.0,'#FF6B6B','高估'),(.7,'#FFC000','低估'),(.5,'#4ECB71','极度低估')),
         '巴菲特指数（A股总市值/上年度全年GDP）','A股总市值/GDP'),
        (erp_rows,[float(r['erp_percent']) for r in erp_rows],
         erp_left,erp_left_ticks,
         (1.4,8.0),[1.4,2.5,3.6,4.7,5.8,6.9,8.0],lambda value:f'{value:.1f}%',((6,'#FFC000',''),),
         '沪深300股债性价比','沪深300风险溢价'),
    )
    for bounds,panel in zip(VALUATION_PLOT_BOUNDS,panels):
        rows,secondary,left_limits,left_ticks,limits,right_ticks,format_right,thresholds,title,secondary_label=panel
        left,top,right,bottom=bounds
        ax=fig.add_axes([left/1408,(1190-bottom)/1190,(right-left)/1408,(bottom-top)/1190],facecolor='black')
        other=ax.twinx()
        x=[date.fromisoformat(r['date']).toordinal() for r in rows]
        line1,=ax.plot(x,[float(r['close']) for r in rows],color=gray,lw=1.8,label='沪深300')
        line2,=other.plot(x,secondary,color=blue,lw=1.8,label=secondary_label)
        for value,color,_ in thresholds:other.axhline(value,color=color,lw=1.25,ls=(0,(5,4)))
        ax.set_xlim(start.toordinal(),end.toordinal());ax.set_ylim(*left_limits)
        other.set_ylim(*limits);other.set_yticks(right_ticks)
        ax.set_yticks(left_ticks)
        ax.set_xticks(ticks)
        ax.grid(axis='y',color='#454545',lw=.7,ls='--')
        for axis in (ax,other):
            axis.tick_params(axis='both',colors=ink,length=3,
                             labelleft=False,labelright=False,labelbottom=False)
            for spine in axis.spines.values():spine.set_color('#777777')
        # Paint labels with the same bundled Pillow font used by the image footer
        # so the output is consistent across Windows and Linux.
    path=Path(out)/'股债性价比.png'
    fig.savefig(path,dpi=100,facecolor='black');plt.close(fig)
    with Image.open(path) as opened:image=opened.convert('RGB')
    draw=ImageDraw.Draw(image)
    title_font=ImageFont.truetype(str(FONT),28)
    axis_font=ImageFont.truetype(str(FONT),16)
    legend_font=ImageFont.truetype(str(FONT),16)
    fallback_font=ImageFont.truetype(str(GLYPH_FALLBACK),16)
    for bounds,panel in zip(VALUATION_PLOT_BOUNDS,panels):
        _,_,left_limits,left_ticks,limits,right_ticks,format_right,thresholds,title,secondary_label=panel
        left,top,right,bottom=bounds
        draw.text((704,top-83),title,font=title_font,fill='white',anchor='mt')
        legend=(('沪深300',gray),(secondary_label,blue))
        width=sum(36+9+legend_font.getlength(label) for label,_ in legend)+34
        cursor=(1408-width)/2
        for label,color in legend:
            draw.line((cursor,top-22,cursor+36,top-22),fill=color,width=2)
            cursor+=45
            draw.text((cursor,top-22),label,font=legend_font,fill='white',anchor='lm')
            cursor+=legend_font.getlength(label)+34
        for value in left_ticks:
            y=bottom-(bottom-top)*(value-left_limits[0])/(left_limits[1]-left_limits[0])
            draw.text((left-13,y),f'{value:,}',font=axis_font,fill=ink,anchor='rm')
        for value in right_ticks:
            y=bottom-(bottom-top)*(value-limits[0])/(limits[1]-limits[0])
            draw.text((right+13,y),format_right(value),font=axis_font,fill=ink,anchor='lm')
        for value,color,label in thresholds:
            if label:
                y=bottom-(bottom-top)*(value-limits[0])/(limits[1]-limits[0])
                if label.startswith('极'):
                    # The existing small PlusBriefs subset omits this glyph.
                    rest=label[1:]
                    x=right-10-fallback_font.getlength('极')-axis_font.getlength(rest)
                    draw.text((x,y-9),'极',font=fallback_font,fill=color,anchor='lb')
                    draw.text((x+fallback_font.getlength('极'),y-9),rest,
                              font=axis_font,fill=color,anchor='lb')
                else:
                    draw.text((right-10,y-9),label,font=axis_font,fill=color,anchor='rb')
        for ordinal,label in zip(ticks,labels):
            x=left+(right-left)*(ordinal-start.toordinal())/(end.toordinal()-start.toordinal())
            draw.text((x,bottom+13),label,font=axis_font,fill=ink,anchor='mt')
    image.save(path)
    return path


def us_line_chart(rows,out,*,filename,title,y_limits,y_ticks,thresholds,tick_format,
                  value_key='pct',years=10,mark_last=False):
    """Single dark panel for the US barometer family; typography and grid follow the other brief charts."""
    from matplotlib.ticker import FuncFormatter
    end=date.fromisoformat(rows[-1]['date'])
    try:start=end.replace(year=end.year-years)
    except ValueError:start=end.replace(year=end.year-years,day=28)
    rows=[r for r in rows if start.isoformat()<=r['date']<=end.isoformat()]
    if len(rows)<2:raise ValueError('美股图表缺少有效历史数据')
    x=list(range(len(rows)))
    fig=plt.figure(figsize=(14.08,7.0),dpi=100,facecolor='black')
    ax=fig.add_axes([.075,.16,.85,.68]);ax.set_facecolor('black')
    ax.plot(x,[float(r[value_key]) for r in rows],color='#22A1D6',lw=1.4)
    for value,color,label in thresholds:
        if y_limits[0]<value<y_limits[1]:
            ax.axhline(value,color=color,lw=1.05,ls=(0,(5,4)))
            ax.annotate(label,xy=(1,value),xycoords=('axes fraction','data'),xytext=(-6,4),
                        textcoords='offset points',ha='right',va='bottom',fontproperties=font(11),color=color)
    if mark_last:
        last=float(rows[-1][value_key])
        ax.axhline(last,color='#22A1D6',lw=1.0,ls=(0,(1.5,3)))
        ax.annotate(tick_format(last),xy=(1,last),xycoords=('axes fraction','data'),xytext=(-6,-14),
                    textcoords='offset points',ha='right',va='bottom',fontproperties=font(12),color='white',
                    bbox=dict(boxstyle='round,pad=0.25',facecolor='#22A1D6',edgecolor='none'))
    ticks=[round((len(rows)-1)*i/7) for i in range(8)]
    ax.set_xlim(0,len(rows)-1);ax.set_ylim(*y_limits);ax.set_yticks(y_ticks)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value,_:tick_format(value)))
    ax.set_xticks(ticks,[rows[i]['date'] for i in ticks])
    ax.set_title(title,fontproperties=font(19),color='white',loc='center',pad=20)
    ax.grid(color='#333333',axis='y',ls='--')
    ax.tick_params(colors='#B6B6B6',labelsize=10)
    for spine in ax.spines.values():spine.set_color('#666666')
    path=Path(out)/filename;fig.savefig(path,dpi=100,facecolor='black');plt.close(fig);return path


def us_barometer_chart(buffett_rows,erp_rows,out):
    """Both US barometer panels on one canvas, mirroring the valuation composite geometry."""
    from matplotlib.ticker import FuncFormatter
    end=date.fromisoformat(erp_rows[-1]['date'])
    try:start=end.replace(year=end.year-10)
    except ValueError:start=end.replace(year=end.year-10,day=28)
    buffett=[r for r in buffett_rows if start.isoformat()<=r['date']<=end.isoformat()]
    erp=[r for r in erp_rows if start.isoformat()<=r['date']<=end.isoformat()]
    if len(buffett)<2 or len(erp)<2:raise ValueError('美股晴雨表缺少有效历史数据')
    values=[float(r['pct']) for r in buffett]
    low=math.floor(min(values)/50)*50;high=math.ceil(max(values)/50)*50
    if high-low<100:high=low+100
    fig=plt.figure(figsize=(14.08,11.90),dpi=100,facecolor='black')
    panels=(
        (buffett,(low,high),list(range(low,high+1,50)),
         ((100,'#FFC000','上限'),(150,'#FF9F43','高估'),(200,'#FF6B6B','很高')),
         '美股巴菲特指数（总市值 / GDP）','美股巴菲特指数',lambda value:f'{value:.0f}%'),
        (erp,(3.5,7.5),[3.5+0.5*i for i in range(9)],
         ((7.0,'#FF6B6B','高位'),(6.0,'#FFC000','均值'),(5.5,'#FF9F43','低位'),(4.5,'#FF6B6B','很低')),
         '美股标普500股权风险溢价（ERP）','标普500股权风险溢价',lambda value:f'{value:.1f}%'),
    )
    for bounds,panel in zip(VALUATION_PLOT_BOUNDS,panels):
        rows,ylim,yticks,thresholds,title,label,tick_format=panel
        left,top,right,bottom=bounds
        ax=fig.add_axes([left/1408,(1190-bottom)/1190,(right-left)/1408,(bottom-top)/1190],facecolor='black')
        x=[date.fromisoformat(r['date']).toordinal() for r in rows]
        ax.plot(x,[float(r['pct']) for r in rows],color='#22A1D6',lw=1.4,label=label)
        for value,color,text in thresholds:
            if ylim[0]<value<ylim[1]:
                ax.axhline(value,color=color,lw=1.05,ls=(0,(5,4)))
                ax.annotate(text,xy=(1,value),xycoords=('axes fraction','data'),xytext=(-6,4),
                            textcoords='offset points',ha='right',va='bottom',fontproperties=font(11),color=color)
        ticks=[start.toordinal()+round((end.toordinal()-start.toordinal())*i/7) for i in range(8)]
        ax.set_xlim(start.toordinal(),end.toordinal());ax.set_ylim(*ylim);ax.set_yticks(yticks)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value,_:tick_format(value)))
        ax.set_xticks(ticks,[date.fromordinal(value).strftime('%Y-%m-%d') for value in ticks])
        ax.set_title(title,fontproperties=font(20),color='white',pad=30)
        ax.legend(prop=font(11),labelcolor='white',loc='lower center',bbox_to_anchor=(0.5,1.0),
                  frameon=False,borderaxespad=0.1)
        ax.grid(color='#333333',axis='y',ls='--')
        ax.tick_params(colors='#B6B6B6',labelsize=10)
        for spine in ax.spines.values():spine.set_color('#777777')
    path=Path(out)/'美股晴雨表.png';fig.savefig(path,dpi=100,facecolor='black');plt.close(fig);return path


def us_structure_chart(rows,out):
    values=[float(r['value']) for r in rows]
    low=math.floor(min(values)/100)*100;high=math.ceil(max(values)/100)*100
    if high-low<200:high=low+200
    return us_line_chart(rows,out,filename='美股市场结构.png',value_key='value',years=1,
        title='标普500等权指数 − 市值加权指数（点）',y_limits=(low,high),
        y_ticks=list(range(low,high+1,100)),thresholds=(),
        tick_format=lambda value:f'{value:,.1f}',mark_last=True)
