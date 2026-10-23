"""Readable, size-bounded page images for a daily issue PDF."""
from __future__ import annotations

import io
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

    # Keep full page resolution and 4:4:4 colour sampling so small text stays legible.
    # Use the highest JPEG quality that fits the per-page size limit.
    for quality in (96, 94, 92, 90, 88, 86):
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=quality, subsampling=0,
                   optimize=True, progressive=True)
        if buffer.tell() <= MAX_IMAGE_BYTES:
            return buffer.getvalue()
    raise ImageSizeError(f'第 {number} 页无法在保持清晰度的前提下压到 1 MB，请更换该页的高复杂度配图')


def image_zip(pdf: Path) -> bytes:
    output = io.BytesIO()
    with pymupdf.open(pdf) as document, ZipFile(output, 'w', compression=ZIP_STORED) as archive:
        for index, page in enumerate(document, start=1):
            name = f'{index}.jpg'
            archive.writestr(name, _page_image(page, index))
    return output.getvalue()
