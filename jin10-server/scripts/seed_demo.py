"""演示数据：把样例日报的素材（含配图）按插件同样的接口推送到服务端。

    python scripts/seed_demo.py http://127.0.0.1:8000 editor@plus.local <密码>
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "2026-09-18" / "issue.json"


def main(base: str, email: str, password: str) -> None:
    http = httpx.Client(base_url=base, timeout=30)
    token = http.post("/api/auth/login", json={"email": email, "password": password}).json()["access_token"]
    http.headers["Authorization"] = f"Bearer {token}"

    issue = json.loads(SAMPLE.read_text(encoding="utf-8"))
    now = dt.datetime.now(dt.UTC)
    materials = []
    for s in issue["sections"]:
        for i, item in enumerate(s["items"]):
            images = []
            for img in item["images"]:
                path = SAMPLE.parent / img["path"]
                url = f"https://img.jin10.com/demo/{path.name}"
                aid = hashlib.sha256(url.encode()).hexdigest()[:32]
                mime = "image/png" if path.suffix == ".png" else "image/jpeg"
                http.put(f"/api/assets/{aid}", content=path.read_bytes(),
                         headers={"Content-Type": mime, "X-Source-Url": quote(url, safe="")}).raise_for_status()
                images.append({"url": url, "source": "plus", "asset_id": aid})
            has_interp = bool(item["interpretation"])
            materials.append({
                "id": str(uuid.uuid4()),
                "source_item_id": f"demo-{s['id']}-{i}",
                "fingerprint": hashlib.sha256(item["title"].encode()).hexdigest(),
                "item_kind": "flash",
                "title": item["title"],
                "flash_title": None,
                "content": item["news"] or "",
                "content_raw": item["news"] or "",
                "plus_title": item["title"] if has_interp else None,
                "has_plus_link": has_interp,
                "images": [],
                "url": "https://www.jin10.com/",
                "raw_html": "<div></div>",
                "collect_time": (now - dt.timedelta(minutes=len(materials))).isoformat(),
                "publish_time": (now - dt.timedelta(hours=2)).isoformat(),
                "collect_method": "context_menu",
                "interpretation_status": "fetched" if has_interp else "none",
                "interpretation": {
                    "plus_id": None,
                    "title": item["title"],
                    "modules": [{"kind": "view", "heading": "PLUS观点", "paragraphs": item["interpretation"], "images": images}],
                    "fetched_at": now.isoformat(),
                } if has_interp else None,
            })
    res = http.post("/api/materials/sync", json={"materials": materials})
    res.raise_for_status()
    print(f"已推送 {len(res.json()['results'])} 条素材")


if __name__ == "__main__":
    main(*sys.argv[1:4])
