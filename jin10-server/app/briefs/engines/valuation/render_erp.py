"""Update only the data area of the supplied workbook's native chart export."""
from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from PIL import Image, ImageDraw
try:
    from .rolling_window import select_window
except ImportError:
    from rolling_window import select_window

HERE=Path(__file__).resolve().parent
BOUNDS=(69,52,1324,530)
ERP_MIN,ERP_MAX=0.014,0.08


def draw_date(image, xy, text):
    spec=json.loads((HERE/'assets/date_glyphs.json').read_text(encoding='utf-8'))
    atlas=Image.open(HERE/'assets/date_glyphs.png').convert('RGBA')
    cursor=xy[0]-sum(spec['glyphs'][c]['advance'] for c in text)/2
    for c in text:
        glyph=spec['glyphs'][c]
        tile=atlas.crop(glyph['box'])
        image.paste(tile,(round(cursor)-4,xy[1]),tile)
        cursor+=glyph['advance']


def render_erp(rows, output_dir, *, threshold=0.06, window_years=10):
    rows,window=select_window(rows,window_years)
    if len(rows)<2 or [r['date'] for r in rows]!=sorted({r['date'] for r in rows}):
        raise ValueError('ERP plot needs at least two unique chronological dates')
    close=np.array([float(r['close']) for r in rows])
    erp=np.array([float(r['erp']) for r in rows])
    if not np.isfinite(close).all() or not np.isfinite(erp).all():
        raise ValueError('Cannot plot nonfinite values')
    if close.min()<0 or close.max()>6000 or erp.min()<ERP_MIN or erp.max()>ERP_MAX:
        raise ValueError('Values exceed native template axes; update both labels and plot limits before publishing')
    original=Image.open(HERE/'assets/erp_native_template.png').convert('RGB')
    if original.size!=(1408,595):
        raise ValueError('Native ERP template dimensions changed')
    left,top,right,bottom=BOUNDS
    width,height=right-left,bottom-top
    x=np.array([date.fromisoformat(r['date']).toordinal() for r in rows])
    x_start=date.fromisoformat(window['calendar_start']).toordinal()
    x_end=date.fromisoformat(window['calendar_end']).toordinal()
    plt.rcParams.update({'path.simplify':False,'agg.path.chunksize':0})
    fig=plt.figure(figsize=(width/100,height/100),dpi=200,facecolor='black')
    ax=fig.add_axes([0,0,1,1],facecolor='black')
    ax.set_xlim(x_start,x_end);ax.set_ylim(0,6000);ax.set_axis_off()
    for value in range(1000,6000,1000):
        ax.axhline(value,color='#B6B6B6',lw=.55,zorder=0)
    ax.plot(x,close,color='#A5A5A5',lw=2.25,zorder=2,solid_capstyle='round')
    scaled=(erp-ERP_MIN)/(ERP_MAX-ERP_MIN)*6000
    line,=ax.plot(x,scaled,color='#22A1D6',lw=2.25,zorder=3,solid_capstyle='round')
    np.testing.assert_array_equal(line.get_ydata(),scaled)
    fraction=None
    if threshold is not None:
        if not ERP_MIN<=threshold<=ERP_MAX:raise ValueError('ERP threshold is outside the right axis')
        threshold_y=(threshold-ERP_MIN)/(ERP_MAX-ERP_MIN)*6000
        ax.axhline(threshold_y,color='#FFC000',lw=2,ls=(0,(4,3)),zorder=1)
        fraction=(ax.transData.transform((x[0],threshold_y))[1]-ax.bbox.y0)/ax.bbox.height
        assert abs(fraction-(threshold-ERP_MIN)/(ERP_MAX-ERP_MIN))<1e-12
    buffer=io.BytesIO();fig.savefig(buffer,format='png',dpi=200,facecolor='black',pad_inches=0);plt.close(fig)
    buffer.seek(0);plot=Image.open(buffer).convert('RGB').resize((width,height),Image.Resampling.LANCZOS)
    result=original.copy();result.paste(plot.crop((1,1,width-1,height-1)),(left+1,top+1))
    allowed=np.zeros((original.height,original.width),bool)
    allowed[top+1:bottom-1,left+1:right-1]=True
    # Preserve the exact native legend lettering and line swatches over the data.
    orig=np.asarray(original);arr=np.asarray(result).copy()
    legend=orig[486:509,520:914]
    ink=legend.max(axis=2)>40
    arr[486:509,520:914][ink]=legend[ink]
    result=Image.fromarray(arr)
    draw=ImageDraw.Draw(result)
    draw.rectangle((18,539,1398,569),fill='black');allowed[539:570,18:1399]=True
    draw.rectangle((left+1,bottom+1,right-1,bottom+7),fill='black');allowed[bottom+1:bottom+8,left:right+1]=True
    positions=np.linspace(x_start,x_end,12)
    labels=[]
    for ordinal in positions:
        day=date.fromordinal(round(ordinal));label=f'{day.year}/{day.month}/{day.day}'
        px=left+width*(ordinal-x_start)/(x_end-x_start)
        draw.line((px,bottom+1,px,bottom+7),fill='#B6B6B6',width=1)
        draw_date(result,(px,543),label);labels.append(label)
    changed=np.any(np.asarray(result)!=orig,axis=2)
    outside=int(np.count_nonzero(changed&~allowed))
    if outside:raise AssertionError(f'{outside} pixels changed outside the allowed data/date areas')
    assert np.array_equal(np.asarray(result)[:45],orig[:45])
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    path=out/f"沪深300股债性价比_近{window_years}年_截至{rows[-1]['date'].replace('-','')}.png"
    result.save(path)
    audit={'size':[1408,595],'points':len(rows),'start':rows[0]['date'],'end':rows[-1]['date'],
           'left_index_axis':[0,6000],'right_erp_axis':[ERP_MIN,ERP_MAX],
           'erp_threshold':threshold,'threshold_fraction_from_bottom':fraction,
           'static_pixels_changed':outside,'title_pixel_identical':True,'legend_from_original':True,
           'extra_smoothing':False,'series_downsampling':False,'date_labels':labels,
           'rolling_window':window,
           'template_origin':'native read-only chart export from supplied workbook',
           'threshold_basis':'fixed 6% from PLUS template explanation; not the isolated STDEVA cell'}
    return path,audit
