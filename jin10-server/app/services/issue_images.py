"""Readable, size-bounded page images for a daily issue PDF."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import pymupdf
from PIL import Image

IMAGE_DPI = 120
MAX_IMAGE_BYTES = 1_000_000


class ImageSizeError(ValueError):
    pass


def _page_image(page: pymupdf.Page, number: int) -> bytes:
    pixmap = page.get_pixmap(dpi=IMAGE_DPI, alpha=False)
    image = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)

    # Crisp text pages usually compress best without losing a single pixel.
    buffer = io.BytesIO()
    image.save(buffer, format='WEBP', lossless=True, method=6)
    if buffer.tell() <= MAX_IMAGE_BYTES:
        return buffer.getvalue()

    # Dense photographs/charts need a high-quality encode to stay below 1 MB.
    # Keep the full pixel dimensions; never shrink a page just to meet the cap.
    for quality in (96, 94, 92):
        buffer = io.BytesIO()
        image.save(buffer, format='WEBP', quality=quality, method=6)
        if buffer.tell() <= MAX_IMAGE_BYTES:
            return buffer.getvalue()
    raise ImageSizeError(f'第 {number} 页无法在保持清晰度的前提下压到 1 MB，请更换该页的高复杂度配图')


def image_zip(pdf: Path, issue_date: date) -> bytes:
    output = io.BytesIO()
    with pymupdf.open(pdf) as document, ZipFile(output, 'w', compression=ZIP_STORED) as archive:
        for index, page in enumerate(document, start=1):
            name = f'PLUS日报-{issue_date:%Y-%m-%d}/第{index:02d}页.webp'
            archive.writestr(name, _page_image(page, index))
    return output.getvalue()
