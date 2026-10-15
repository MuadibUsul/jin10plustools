"""Rebuild the tiny open-licensed fallback for the valuation label 极度低估.

Source: Google Fonts Noto Sans SC, SIL OFL 1.1. Pass the downloaded
NotoSansSC[wght].ttf as argv[1]. Only the missing 极 glyph is retained.
"""
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont


source = Path(sys.argv[1])
target = Path(__file__).resolve().parents[1] / 'jin10-server/app/briefs/assets/PlusGlyphFallback.ttf'
font = TTFont(source)
glyphs = subset.Subsetter()
glyphs.populate(text='极')
glyphs.subset(font)
for record in font['name'].names:
    if record.nameID in (1, 4, 6, 16):
        value = 'PlusGlyphFallback' if record.nameID != 6 else 'PlusGlyphFallback-Regular'
        font['name'].setName(value, record.nameID, record.platformID, record.platEncID, record.langID)
target.parent.mkdir(parents=True, exist_ok=True)
font.save(target)
assert ord('极') in TTFont(target).getBestCmap()
print(target, target.stat().st_size)
