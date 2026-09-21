"""PPTX → PDF。

- 服务端（Docker）：LibreOffice 无头模式（镜像内已安装思源同款字体 Noto CJK 并做了字体名映射）
- Windows 开发机没有 LibreOffice 时：调用本机 PowerPoint 导出（仅用于本地调试）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = 180


class PdfConversionError(RuntimeError):
    pass


def _soffice() -> str | None:
    return shutil.which("soffice") or shutil.which("libreoffice")


_UNO_PYTHON = "/usr/bin/python3"
_UNO_SCRIPT = Path(__file__).with_name("lo_convert.py")


def _uno_available() -> bool:
    if not Path(_UNO_PYTHON).is_file():
        return False
    return subprocess.run([_UNO_PYTHON, "-c", "import uno"], capture_output=True).returncode == 0


def _convert_uno(pptx: Path, out_dir: Path) -> Path:
    """经 UNO 转换并关闭中西文自动间距，使 PDF 与 PowerPoint 字距一致"""
    pdf = out_dir / f"{pptx.stem}.pdf"
    try:
        subprocess.run(
            [_UNO_PYTHON, str(_UNO_SCRIPT), str(pptx), str(pdf)], check=True, capture_output=True, timeout=TIMEOUT_SECONDS
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        detail = getattr(err, "stderr", b"") or b""
        raise PdfConversionError(f"LibreOffice 转换失败：{detail.decode(errors='ignore')[-300:] or err}") from err
    return pdf


def _convert_libreoffice(binary: str, pptx: Path, out_dir: Path) -> Path:
    # 每次使用独立的用户配置目录，允许并发转换
    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
        cmd = [
            binary,
            f"-env:UserInstallation=file:///{Path(profile).as_posix().lstrip('/')}",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(pptx),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=TIMEOUT_SECONDS)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
            raise PdfConversionError(f"LibreOffice 转换失败：{err}") from err
    return out_dir / f"{pptx.stem}.pdf"


def _convert_powerpoint(pptx: Path, out_dir: Path) -> Path:
    pdf = out_dir / f"{pptx.stem}.pdf"
    script = (
        "$pp = New-Object -ComObject PowerPoint.Application; "
        f"$p = $pp.Presentations.Open('{pptx.resolve()}', $true, $false, $false); "
        f"$p.SaveAs('{pdf.resolve()}', 32); $p.Close(); $pp.Quit()"
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=TIMEOUT_SECONDS
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as err:
        raise PdfConversionError(f"PowerPoint 转换失败：{err}") from err
    return pdf


def pptx_to_pdf(pptx: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    binary = _soffice()
    if binary and _uno_available():
        pdf = _convert_uno(pptx, out_dir)
    elif binary:
        pdf = _convert_libreoffice(binary, pptx, out_dir)
    elif sys.platform == "win32" and os.environ.get("PLUS_PDF_USE_POWERPOINT", "1") == "1":
        pdf = _convert_powerpoint(pptx, out_dir)
    else:
        raise PdfConversionError("未安装 LibreOffice，无法生成 PDF")
    if not pdf.is_file():
        raise PdfConversionError("PDF 未生成")
    return pdf
