"""Check the actual server PDF renderer, including its CJK font substitution."""

from __future__ import annotations

import datetime as dt
import shutil

import fitz
import pytest

from app.ppt.generator import generate
from app.ppt.models import DailyIssue, DailyItem, DailySection, SectionId
from app.ppt.pdf import pptx_to_pdf


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice is not installed")
def test_article_title_is_bold_in_libreoffice_pdf(tmp_path) -> None:
    title = "企业涨价推动通胀回升"
    issue = DailyIssue(
        date=dt.date(2026, 10, 23),
        sections=[DailySection(id=SectionId.MACRO, items=[DailyItem(title=title, news="正文保持原样")])],
    )
    pptx = tmp_path / "daily.pptx"
    generate(issue, pptx)
    pdf = pptx_to_pdf(pptx, tmp_path)

    with fitz.open(pdf) as document:
        page = document[1]
        spans = [span for block in page.get_text("dict")["blocks"] if "lines" in block
                 for line in block["lines"] for span in line["spans"]]
    title_spans = [span for span in spans if title in span["text"]]
    assert title_spans, "LibreOffice PDF did not contain the article title"
    assert all(span["flags"] & 16 or "bold" in span["font"].lower() for span in title_spans), title_spans

