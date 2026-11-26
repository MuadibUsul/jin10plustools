"""Shared cached raster date-glyph blitter for native template charts."""
from __future__ import annotations

import functools
import json
from pathlib import Path

from PIL import Image


@functools.lru_cache(maxsize=4)
def _cached_atlas(assets_dir_str: str) -> tuple[dict, Image.Image]:
    assets_dir = Path(assets_dir_str)
    spec = json.loads((assets_dir / 'date_glyphs.json').read_text(encoding='utf-8'))
    with Image.open(assets_dir / 'date_glyphs.png') as opened:
        atlas = opened.convert('RGBA')
        atlas.load()
    return spec, atlas.copy()


def draw_date_glyphs(image: Image.Image, xy: tuple[float, int], text: str, *,
                     assets_dir: Path, x_offset: int) -> None:
    """Blit portable Arial 14 raster date glyphs centered at *xy* with *x_offset*."""
    spec, atlas = _cached_atlas(str(Path(assets_dir).resolve()))
    width = sum(spec['glyphs'][char]['advance'] for char in text)
    cursor = xy[0] - width / 2
    for char in text:
        glyph = spec['glyphs'][char]
        tile = atlas.crop(glyph['box'])
        image.paste(tile, (round(cursor) + x_offset, xy[1]), tile)
        cursor += glyph['advance']
