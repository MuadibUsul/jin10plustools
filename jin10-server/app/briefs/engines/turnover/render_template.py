"""Keep the supplied template pixels; update only curves and date-axis bands."""
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

HERE = Path(__file__).resolve().parent
BG = (4,12,15)
CYAN = '#12E8EB'
GOLD = '#FFC44B'


def draw_date(image, xy, text):
    """Portable raster glyphs preserve Arial 14 date lettering without OS fonts."""
    spec = json.loads((HERE/'assets/date_glyphs.json').read_text(encoding='utf-8'))
    atlas = Image.open(HERE/'assets/date_glyphs.png').convert('RGBA')
    width = sum(spec['glyphs'][c]['advance'] for c in text)
    cursor = xy[0] - width/2
    for char in text:
        glyph = spec['glyphs'][char]
        tile = atlas.crop(glyph['box'])
        image.paste(tile, (round(cursor)-3, xy[1]), tile)
        cursor += glyph['advance']


def render_template(rows, output_dir):
    template_path = HERE/'assets/turnover_template.png'
    original = Image.open(template_path).convert('RGB')
    if original.size != (1118,1407):
        raise ValueError('The supplied template must remain 1118 x 1407')
    image = original.copy()
    edited = Image.new('1', original.size, 0)
    edited_draw = ImageDraw.Draw(edited)
    # The supplied plate embeds branding across the lower plot/axis. Rebuild that
    # area before drawing data and dates; final branding belongs to image_delivery.
    ImageDraw.Draw(image).rectangle((765,1318,1067,1360),fill=BG)
    image.paste(original.crop((1035,1290,1039,1310)),(1035,1318))
    image.paste(original.crop((765,630,1037,633)),(765,1337))
    image.paste(original.crop((1039,620,1090,646)),(1039,1327))
    edited_draw.rectangle((765,1318,1089,1360),fill=1)
    x = np.arange(len(rows))
    closes = np.array([float(r['index_close']) for r in rows])
    if closes.max() > 6000 or closes.min() < 0:
        raise ValueError('Index exceeds the fixed template 0–6000 axis; update template axes before rendering')
    ticks = np.rint(np.linspace(0,len(rows)-1,12)).astype(int).tolist()
    dates = [date.fromisoformat(rows[i]['date']) for i in ticks]
    labels = [f'{d.year}/{d.month}/{d.day}' for d in dates]
    panel_audit = []
    plt.rcParams.update({'path.simplify':False,'agg.path.chunksize':0})
    for field, inverse, threshold, left, top, right, bottom, date_top, date_band in [
        ('low_ma9', True, 96, 64,127,1037,631,648,(646,666)),
        ('high_ma9',False,17,64,832,1036,1338,1358,(1356,1376)),
    ]:
        width,height = right-left,bottom-top
        fig = plt.figure(figsize=(width/100,height/100),dpi=200,facecolor=tuple(c/255 for c in BG))
        ax = fig.add_axes([0,0,1,1],facecolor=tuple(c/255 for c in BG))
        ax.set_xlim(0,len(rows)-1)
        ax.set_ylim((100,0) if inverse else (0,100))
        ax.set_axis_off()
        for grid_y in [20,40,60,80]:
            ax.axhline(grid_y,color='#283338',lw=.50,ls=(0,(2.5,2.5)),zorder=0)
        index_y = 100*(1-closes/6000) if inverse else 100*closes/6000
        ax.plot(x,index_y,color=GOLD,lw=1.17,zorder=2,solid_capstyle='round')
        ratio = np.array([float(r[field]) for r in rows])
        ratio_line, = ax.plot(x,ratio,color=CYAN,lw=1.17,zorder=3,solid_capstyle='round')
        ax.axhline(threshold,color='#DED6BD',lw=1.15,ls=(0,(6,4)),zorder=1)
        # Verify plotting coordinates before compositing with the fixed template.
        np.testing.assert_array_equal(ratio_line.get_ydata(),ratio)
        fraction = (ax.transData.transform((0,threshold))[1]-ax.bbox.y0)/ax.bbox.height
        expected = 1-threshold/100 if inverse else threshold/100
        assert abs(fraction-expected)<1e-10
        buffer=io.BytesIO()
        fig.savefig(buffer,format='png',dpi=200,facecolor=fig.get_facecolor(),pad_inches=0)
        plt.close(fig)
        buffer.seek(0)
        plotted=Image.open(buffer).convert('RGB').resize((width,height),Image.Resampling.LANCZOS)
        # Preserve all original axis strokes and axis numbers, including 96/17.
        inset=1
        part=plotted.crop((inset,inset,width-inset,height-inset))
        image.paste(part,(left+inset,top+inset))
        edited_draw.rectangle((left+inset,top+inset,right-inset-1,bottom-inset-1),fill=1)
        draw=ImageDraw.Draw(image)
        # Replace only data-dependent x tick strokes and date glyphs.
        tick_strip=(left+1,bottom+1,right-1,bottom+8)
        draw.rectangle(tick_strip,fill=BG)
        edited_draw.rectangle((left,bottom+1,right,bottom+8),fill=1)
        date_strip=(22,date_band[0],1098,date_band[1]-1)
        draw.rectangle(date_strip,fill=BG)
        edited_draw.rectangle(date_strip,fill=1)
        for i,label in zip(ticks,labels):
            px=left+(right-left)*i/(len(rows)-1)
            draw.line((px,bottom+1,px,bottom+7),fill=(155,166,174),width=1)
            draw_date(image,(px,date_top),label)
        panel_audit.append({'field':field,'plot_bounds':[left,top,right,bottom],
                            'threshold':threshold,'threshold_fraction_from_bottom':expected,
                            'date_labels':labels,'extra_smoothing':False})

    # Branding is added once by the shared final-image delivery service.
    source=np.asarray(original)
    result=np.asarray(image).copy()
    # Static pixels (titles, legends, borders, axis labels) must not change.
    different=np.any(result!=source,axis=2)
    outside=int(np.count_nonzero(different & ~np.asarray(edited,dtype=bool)))
    if outside:
        raise AssertionError(f'{outside} changed pixels outside permitted curve/date regions')
    for bounds in [(0,0,1118,117),(0,702,1118,820)]:
        assert np.array_equal(np.asarray(image.crop(bounds)),np.asarray(original.crop(bounds)))
    output_dir.mkdir(parents=True,exist_ok=True)
    png=output_dir/f"换手率_{rows[0]['date'].replace('-','')}至{rows[-1]['date'].replace('-','')}_原模板版.png"
    image.save(png)
    geometry={'low_96_fraction_from_bottom':.04,'high_17_fraction_from_bottom':.17,
              'low_latest_fraction_from_bottom':1-float(rows[-1]['low_ma9'])/100,
              'high_latest_fraction_from_bottom':float(rows[-1]['high_ma9'])/100}
    audit={'png_size':[1118,1407],'template_asset':str(template_path),
           'template_mode':'original static plate + code-rendered curve blocks and dynamic date bands',
           'static_pixels_changed':outside,'headers_and_legends_pixel_identical':True,
           'panel_geometry':panel_audit,'date_font':'Arial 14 (bundled raster glyphs)'}
    return png,geometry,6000,audit
