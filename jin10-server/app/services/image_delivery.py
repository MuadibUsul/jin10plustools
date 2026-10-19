"""Shared final-image contract for briefs and future fresh/annotation renderers."""
import functools
import hashlib
import io
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parents[1]
WATERMARK = ASSETS / 'assets/plus-watermark.png'
FONT = ASSETS / 'briefs/assets/PlusBriefs.ttf'
GLYPH_FALLBACK = ASSETS / 'briefs/assets/PlusGlyphFallback.ttf'
# The bundled brand font is a subset; notes may use characters it omits.
SYSTEM_CJK = (
    Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
    Path('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc'),
    Path('C:/Windows/Fonts/msyh.ttc'),
    Path('C:/Windows/Fonts/simhei.ttf'),
)
MAX_BYTES = 266_000  # Also satisfies a 266 KiB limit.
POLICY_VERSION = '4'


@functools.lru_cache(maxsize=1)
def delivery_signature():
    """Content hash of the delivery composition and its fonts.

    Download caches key on this signature, so a changed notes band, encoder or font
    can never serve an image composed by older code. Policy bumps stay optional.
    """
    digest = hashlib.sha256(POLICY_VERSION.encode())
    files = [Path(__file__), FONT, GLYPH_FALLBACK]
    for path in files:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    for path in SYSTEM_CJK:
        if path.is_file():
            stat = path.stat()
            digest.update(f'{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return digest.hexdigest()[:16]


def glyph_coverage(path):
    try:
        from fontTools.ttLib import TTFont
        font = TTFont(str(path))
        covered = set()
        for table in font['cmap'].tables:
            covered |= set(table.cmap)
        return covered
    except Exception:
        return None


class NoteFonts:
    """Brand font first; omitted glyphs fall back to the bundled subset and then to a
    system CJK font so every editable note character stays legible."""

    def __init__(self, size):
        self.primary = ImageFont.truetype(str(FONT), size)
        self.secondary = ImageFont.truetype(str(GLYPH_FALLBACK), size) if GLYPH_FALLBACK.is_file() else None
        self.fallback = None
        fallback_path = None
        for path in SYSTEM_CJK:
            if path.is_file():
                try:
                    self.fallback = ImageFont.truetype(str(path), size)
                    fallback_path = path
                    break
                except OSError:
                    continue
        self.coverage = {id(self.primary): glyph_coverage(FONT),
                         id(self.secondary): glyph_coverage(GLYPH_FALLBACK) if self.secondary else set()}
        if self.fallback is not None:
            self.coverage[id(self.fallback)] = glyph_coverage(fallback_path)

    def font_for(self, char):
        if char in '\n\r':
            return self.primary
        code = ord(char)
        primary = self.coverage.get(id(self.primary))
        if primary is None or code in primary:
            return self.primary
        if self.secondary is not None and code in (self.coverage.get(id(self.secondary)) or ()):
            return self.secondary
        return self.fallback or self.primary

    def covered(self, char):
        font = self.font_for(char)
        coverage = self.coverage.get(id(font))
        return coverage is None or ord(char) in coverage

    def length(self, text):
        return sum(self.font_for(char).getlength(char) for char in text)

    def draw(self, draw, xy, text, fill):
        x, y = xy
        baseline = y + self.primary.getmetrics()[0]
        for char in text:
            font = self.font_for(char)
            draw.text((x, baseline), char, font=font, fill=fill, anchor='ls')
            x += font.getlength(char)


def download_name(day, title, suffix):
    from app.services.copy_dates import copy_dates
    title = copy_dates(title)
    stamp = date.fromisoformat(str(day)).strftime('%Y%m%d')
    digest = hashlib.md5(title.encode('utf-8'), usedforsecurity=False).hexdigest()
    return f'{stamp}（{digest}）{suffix}'


def wrapped_lines(text, fonts, width):
    lines = []
    for paragraph in text.splitlines():
        line = ''
        for char in paragraph:
            if line and fonts.length(line + char) > width:
                lines.append(line)
                line = ''
            line += char
        lines.append(line)
    return lines


def compose_image(source, *, notes='', dark=False, remove_valuation_source=False, reuse_footer=False,
                  turnover_snapshot=None):
    """Use native pixels; add readable notes and an unobstructed original watermark."""
    with Image.open(source) as opened:
        original = opened.convert('RGB')
    if turnover_snapshot is not None:
        # Old editions retain their original bytes and hashes. Rebuild from their
        # own captured counts so removing branding never erases chart lines.
        import re
        import subprocess
        import sys
        import tempfile
        from app.briefs.engines.turnover.build_turnover import load_and_calculate
        match = re.search(r'_(\d{8})至(\d{8})_', Path(source).name)
        if not match or not Path(turnover_snapshot).is_file():
            raise ValueError('换手率旧图缺少对应数据快照，无法安全移除旧水印，请重新取数生成')
        start, end = (date.fromisoformat(value).isoformat() for value in match.groups())
        rows = [row for row in load_and_calculate(Path(turnover_snapshot)) if start <= row['date'] <= end]
        if len(rows) < 2 or rows[0]['date'] != start or rows[-1]['date'] != end:
            raise ValueError('换手率快照日期与旧图不一致，无法安全重绘')
        with tempfile.TemporaryDirectory(prefix='turnover-delivery-') as tmp:
            # Keep matplotlib's global state out of concurrent web requests, as
            # in the regular turnover job runner. This command only reads data.
            command = [sys.executable, str(ASSETS / 'briefs/engines/turnover/build_turnover.py'),
                       '--data', str(Path(turnover_snapshot).resolve()), '--start', start,
                       '--as-of', end, '--out-dir', tmp]
            try:
                done = subprocess.run(command, capture_output=True, timeout=90)
            except subprocess.TimeoutExpired:
                raise ValueError('换手率历史快照重绘超时，请稍后重试') from None
            if done.returncode:
                raise ValueError('换手率历史快照重绘失败，未请求外部数据')
            rebuilt = next(Path(tmp).glob('换手率*.png'))
            with Image.open(rebuilt) as opened:
                original = opened.convert('RGB')
    width, height = original.size
    background = '#000000' if dark else '#FFFFFF'
    if remove_valuation_source:
        # Legacy composite: its upper chart occupies exactly half the original image.
        # Only the source-caption band below all axis labels is cleared.
        ImageDraw.Draw(original).rectangle((0, round(height * .45), width, height // 2 - 1), fill='black')
        # Normalize both headings on the merged canvas, including archived templates.
        # These bands contain headings only; chart axes and data stay untouched.
        draw = ImageDraw.Draw(original)
        title_font = ImageFont.truetype(str(FONT), round(width * 28 / 1408))
        for top, band, title in (
            (0, round(height * .16 / 2), '巴菲特指数（A股总市值/上年度全年GDP）'),
            (height // 2, round(height * .075 / 2), '沪深300股债性价比'),
        ):
            draw.rectangle((0, top, width, top + band - 1), fill='black')
            draw.text((width / 2, top + round(width * 12 / 1408)), title,
                      font=title_font, fill='white', anchor='mt')
    margin = max(18, round(width * .03))
    font_size = max(20, round(width / 58))
    fonts = NoteFonts(font_size)
    lines = wrapped_lines(notes, fonts, width - 2 * margin) if notes else []
    line_height = round(font_size * 1.55)
    with Image.open(WATERMARK) as asset:
        logo = asset.convert('RGBA')
    logo_width = min(340, max(120, round(width * .23)), width - 2 * margin)
    logo = logo.resize((logo_width, round(logo.height * logo_width / logo.width)), Image.Resampling.LANCZOS)
    if reuse_footer and not notes:
        # Liquidity already reserves the right side of its plain footer for branding.
        ImageDraw.Draw(original).rectangle((round(width * .7), height - 180, width, height),
                                          fill=original.getpixel((0,0)))
        original.paste(logo,(width-margin-logo.width,height-margin-logo.height),logo)
        return original
    footer_height = (len(lines) * line_height + margin if lines else 0) + logo.height + 2 * margin
    result = Image.new('RGB', (width, height + footer_height), background)
    result.paste(original, (0, 0))
    draw = ImageDraw.Draw(result)
    for index, line in enumerate(lines):
        fonts.draw(draw, (margin, height + margin + index * line_height), line,
                   '#D2D2D2' if dark else '#334155')
    result.paste(logo, (width - margin - logo.width, result.height - margin - logo.height), logo)
    return result


def encode_image(image):
    """Keep dimensions first; never export a low-quality or oversized result."""
    # Chart text and thin lines usually fit as indexed PNG without resizing.
    def png(value):
        buffer = io.BytesIO()
        value.save(buffer, format='PNG', optimize=True)
        return buffer.getvalue()
    for candidate in (image, image.quantize(colors=256, dither=Image.Dither.NONE)):
        content = png(candidate)
        if len(content) <= MAX_BYTES:
            return content, '.png', 'image/png'
    # Photographic/AI images: high-quality 4:4:4 JPEG, then modest size reductions.
    # Do not shrink small charts or use aggressive quality settings to force a fit.
    floor = min(image.width, 1280)
    width = image.width
    while True:
        candidate = image if width == image.width else image.resize(
            (width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
        for quality in (95, 92, 90, 88):
            buffer = io.BytesIO()
            candidate.save(buffer, format='JPEG', quality=quality, subsampling=0,
                           optimize=True, progressive=True)
            content = buffer.getvalue()
            if len(content) <= MAX_BYTES:
                return content, '.jpg', 'image/jpeg'
        if width == floor:
            raise ValueError('图片在保持清晰度的条件下仍超过266KB，请调整图片内容或排版后重新生成')
        width = max(floor, round(width * .9))


def store_image(source, destination, *, notes='', dark=False, remove_valuation_source=False, reuse_footer=False,
                turnover_snapshot=None):
    image = compose_image(source, notes=notes, dark=dark, remove_valuation_source=remove_valuation_source,
                          reuse_footer=reuse_footer,turnover_snapshot=turnover_snapshot)
    content, suffix, media_type = encode_image(image)
    destination = Path(destination).with_suffix(suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Atomic publication also tolerates two readers preparing the same version.
    import uuid
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_bytes(content)
    temporary.replace(destination)
    return destination, media_type
