"""首页推荐只决定入选标题，不改变正文的栏目或顺序。"""
from collections.abc import Iterable

from app.db.models import Material

COVER_LIMIT = 12


def recommend_cover_ids(materials: Iterable[Material]) -> list[str]:
    ranked = sorted(materials, key=lambda m: (-m.importance_score, -m.final_score, str(m.id)))
    return [str(m.id) for m in ranked[:COVER_LIMIT]]
