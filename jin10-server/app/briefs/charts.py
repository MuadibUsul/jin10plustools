"""Code-only charts; native templates remain in the engines package."""
from pathlib import Path
from datetime import date
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

FONT=Path(__file__).parent/'assets/PlusBriefs.ttf'


def font(size=12):return FontProperties(fname=str(FONT),size=size)


def volume_chart(rows,out):
    rows=rows[-75:];x=list(range(len(rows)));last=rows[-1]
    fig=plt.figure(figsize=(12,14.6),dpi=100,facecolor='white')
    ink='#17233B';muted='#7D8798';blue='#5476DC';green='#27A86C'
    def text(x,y,s,size=15,color=ink):fig.text(x,y,s,fontproperties=font(size),color=color)
    text(.07,.96,'PLUS 快讯 / A股温度计',15,muted);text(.07,.905,'沪深两市成交额',32)
    text(.07,.81,f"{float(last['turn'])/10000:.2f}",82,blue);text(.36,.82,'万亿元',23)
    text(.07,.77,f"数据日：{last['date']}    20日均线：{float(last['ma20'])/10000:.2f}万亿元",16,muted)
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
