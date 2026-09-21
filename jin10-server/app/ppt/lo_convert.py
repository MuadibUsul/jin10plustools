"""LibreOffice UNO 转换脚本：PPTX → PDF，并关闭「中西文之间自动加空隙」。

LibreOffice 默认在汉字与英文 / 数字之间自动加宽（ParaIsCharacterDistance），PowerPoint 没有这个行为；
不关闭会导致 PDF 与 PPT 字距不一致（如「SK 海力士」「04 AI 热点」），并使行宽超出按 PowerPoint 校准的测算。

由系统自带 python3（含 python3-uno）运行，不依赖应用环境：
    /usr/bin/python3 lo_convert.py input.pptx output.pdf
"""

import os
import subprocess
import sys
import tempfile
import time

import uno  # type: ignore[import-not-found]
from com.sun.star.beans import PropertyValue  # type: ignore[import-not-found]
from com.sun.star.connection import NoConnectException  # type: ignore[import-not-found]


def prop(name, value):
    p = PropertyValue()
    p.Name, p.Value = name, value
    return p


def disable_autospace(shape):
    if shape.supportsService("com.sun.star.drawing.GroupShape"):
        for i in range(shape.getCount()):
            disable_autospace(shape.getByIndex(i))
        return
    if not shape.supportsService("com.sun.star.drawing.Text"):
        return
    paragraphs = shape.getText().createEnumeration()
    while paragraphs.hasMoreElements():
        para = paragraphs.nextElement()
        if para.getPropertySetInfo().hasPropertyByName("ParaIsCharacterDistance"):
            para.setPropertyValue("ParaIsCharacterDistance", False)


def main(src, dst):
    pipe = f"plus_lo_{os.getpid()}"
    with tempfile.TemporaryDirectory(prefix="lo-profile-") as profile:
        office = subprocess.Popen(
            [
                "soffice",
                f"-env:UserInstallation=file://{profile}",
                "--headless",
                "--invisible",
                "--nologo",
                "--norestore",
                f"--accept=pipe,name={pipe};urp;",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            local = uno.getComponentContext()
            resolver = local.ServiceManager.createInstanceWithContext("com.sun.star.bridge.UnoUrlResolver", local)
            ctx = None
            for _ in range(120):
                try:
                    ctx = resolver.resolve(f"uno:pipe,name={pipe};urp;StarOffice.ComponentContext")
                    break
                except NoConnectException:
                    time.sleep(0.25)
            if ctx is None:
                raise RuntimeError("LibreOffice 启动超时")
            desktop = ctx.ServiceManager.createInstanceWithContext("com.sun.star.frame.Desktop", ctx)
            doc = desktop.loadComponentFromURL(uno.systemPathToFileUrl(os.path.abspath(src)), "_blank", 0, (prop("Hidden", True),))
            try:
                pages = doc.getDrawPages()
                for i in range(pages.getCount()):
                    page = pages.getByIndex(i)
                    for j in range(page.getCount()):
                        disable_autospace(page.getByIndex(j))
                doc.storeToURL(uno.systemPathToFileUrl(os.path.abspath(dst)), (prop("FilterName", "impress_pdf_Export"),))
            finally:
                doc.close(True)
            try:
                desktop.terminate()
            except Exception:  # 连接会随进程退出而断开
                pass
        finally:
            try:
                office.wait(timeout=20)
            except subprocess.TimeoutExpired:
                office.kill()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
