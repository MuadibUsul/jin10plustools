"""Shared cached TrueType/OpenType/TTC Unicode coverage helper."""
from __future__ import annotations

import functools


@functools.lru_cache(maxsize=16)
def font_cmap(path_str: str) -> frozenset[int] | None:
    """Return Unicode codepoints covered by face 0 of *path_str*, or None if unreadable."""
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(str(path_str), fontNumber=0, lazy=True)
        try:
            covered: set[int] = set(font.getBestCmap() or {})
            if 'cmap' in font:
                for table in font['cmap'].tables:
                    if table.isUnicode() and table.cmap:
                        covered.update(table.cmap)
            return frozenset(covered)
        finally:
            font.close()
    except Exception:
        return None
