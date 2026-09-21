"""文件存储（本地磁盘 / Docker 数据卷）。路径：<storage_dir>/<team_id>/assets/<id[:2]>/<id>"""

from __future__ import annotations

import os
import tempfile
import uuid
from pathlib import Path

from app.config import get_settings


def asset_path(team_id: uuid.UUID, asset_id: str) -> Path:
    return get_settings().storage_dir / str(team_id) / "assets" / asset_id[:2] / asset_id


def write_atomic(path: Path, data: bytes) -> None:
    """先写临时文件再改名，避免中途失败留下半个文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
