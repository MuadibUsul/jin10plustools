"""Shared final-image contract for briefs and future fresh/annotation renderers."""
from __future__ import annotations

from datetime import date
import functools
import hashlib
import io
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from app.briefs.font_cmap import font_cmap

ASSETS = Path(__file__).parents[1]
WATERMARK = ASSETS / 'assets/plus-watermark.png'
FONT = ASSETS / 'briefs/assets/PlusBriefs.ttf'
GLYPH_FALLBACK = ASSETS / 'briefs/assets/PlusGlyphFallback.ttf'
FONT_CMAP_MODULE = ASSETS / 'briefs/font_cmap.py'
BUILD_TURNOVER = ASSETS / 'briefs/engines/turnover/build_turnover.py'
# The bundled brand font is a subset; notes may use characters it omits.
SYSTEM_CJK = (
    Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'),
    Path('/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc'),
    Path('C:/Windows/Fonts/msyh.ttc'),
    Path('C:/Windows/Fonts/simhei.ttf'),
)
MAX_BYTES = 266_000  # Also satisfies a 266 KiB limit.
POLICY_VERSION = '4'


@functools.lru_cache(maxsize=4)
def _delivery_signature_cached(watermark_key: tuple[str, int, int]) -> str:
    digest = hashlib.sha256(POLICY_VERSION.encode())
    watermark_path = Path(watermark_key[0])
    files = [Path(__file__), FONT, GLYPH_FALLBACK, watermark_path, FONT_CMAP_MODULE, BUILD_TURNOVER]
    for path in files:
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    for path in SYSTEM_CJK:
        if path.is_file():
            stat = path.stat()
            digest.update(f'{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode())
    return digest.hexdigest()[:16]


def delivery_signature() -> str:
    """Content hash of the delivery composition, watermark, turnover builder, and fonts.

    Download caches key on this signature, so a changed notes band, watermark, encoder
    or font can never serve an image composed by older code. Policy bumps stay optional.
    """
    watermark_path = Path(WATERMARK)
    if watermark_path.is_file():
        stat = watermark_path.stat()
        key = (str(watermark_path), stat.st_size, stat.st_mtime_ns)
    else:
        key = (str(watermark_path), -1, -1)
    return _delivery_signature_cached(key)


delivery_signature.cache_clear = _delivery_signature_cached.cache_clear  # type: ignore[attr-defined]


def glyph_coverage(path):
    return font_cmap(str(path))


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
        self.coverage = {
            id(self.primary): font_cmap(str(FONT)),
            id(self.secondary): font_cmap(str(GLYPH_FALLBACK)) if self.secondary else frozenset(),
        }
        if self.fallback is not None and fallback_path is not None:
            self.coverage[id(self.fallback)] = font_cmap(str(fallback_path))
        self._width_cache: dict[str, float] = {}

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

    def char_width(self, char):
        cached = self._width_cache.get(char)
        if cached is None:
            cached = self.font_for(char).getlength(char)
            self._width_cache[char] = cached
        return cached

    def length(self, text):
        return sum(self.char_width(char) for char in text)

    def draw(self, draw, xy, text, fill):
        x, y = xy
        baseline = y + self.primary.getmetrics()[0]
        for char in text:
            font = self.font_for(char)
            draw.text((x, baseline), char, font=font, fill=fill, anchor='ls')
            x += self.char_width(char)


def download_name(day, title, suffix):
    from app.services.copy_dates import copy_dates
    title = copy_dates(title)
    stamp = date.fromisoformat(str(day)).strftime('%Y%m%d')
    digest = hashlib.md5(title.encode('utf-8'), usedforsecurity=False).hexdigest()
    ext = '' if not suffix else (suffix if suffix.startswith('.') else f'.{suffix}')
    return f'{stamp}（{digest}）{ext}'


def wrapped_lines(text, fonts, width):
    lines = []
    measure = fonts.char_width if hasattr(fonts, 'char_width') else fonts.length
    for paragraph in text.splitlines():
        line_chars: list[str] = []
        line_width = 0.0
        for char in paragraph:
            char_w = measure(char)
            if line_chars and line_width + char_w > width:
                lines.append(''.join(line_chars))
                line_chars = [char]
                line_width = char_w
            else:
                line_chars.append(char)
                line_width += char_w
        lines.append(''.join(line_chars))
    return lines


def _open_rgb(source, *, dark: bool) -> Image.Image:
    with Image.open(source) as opened:
        has_alpha = opened.mode in ('RGBA', 'LA') or (opened.mode == 'P' and 'transparency' in opened.info)
        if has_alpha:
            rgba = opened.convert('RGBA')
            canvas = Image.new('RGBA', rgba.size, '#0B0D12' if dark else '#FFFFFF')
            canvas.alpha_composite(rgba)
            return canvas.convert('RGB')
        return opened.convert('RGB')


def compose_image(source, *, notes='', dark=False, remove_valuation_source=False, reuse_footer=False,
                  turnover_snapshot=None):
    """Use native pixels; add readable notes and an unobstructed original watermark."""
    original = _open_rgb(source, dark=dark)
    if turnover_snapshot is not None:
        # Old editions retain their original bytes and hashes. Rebuild from their
        # own captured counts so removing branding never erases chart lines.
        import re
        import subprocess
        import sys
        import tempfile
        from app.briefs import trading_calendar
        from app.briefs.engines.turnover.build_turnover import load_and_calculate

        snap_path = Path(turnover_snapshot)
        match = re.search(r'_(\d{8})至(\d{8})_', Path(source).name)
        if not match or not snap_path.is_file():
            raise ValueError('换手率旧图缺少对应数据快照，无法安全移除旧水印，请重新取数生成')
        start, end = (date.fromisoformat(value).isoformat() for value in match.groups())
        archived_calendar = snap_path.with_name('turnover-calendar.json')
        session_dates = None
        synthetic_non_sessions = False
        if archived_calendar.is_file():
            session_dates = json.loads(archived_calendar.read_text(encoding='utf-8'))['sessions']
        else:
            raw_rows = json.loads(snap_path.read_text(encoding='utf-8'))
            if isinstance(raw_rows, list) and raw_rows:
                ordered_dates = sorted(str(item['date']) for item in raw_rows if isinstance(item, dict) and 'date' in item)
                if ordered_dates:
                    try:
                        session_dates = trading_calendar.sessions('xshg', ordered_dates[0], ordered_dates[-1])
                        synthetic_non_sessions = any(
                            not trading_calendar.is_session('xshg', d) for d in ordered_dates
                        )
                    except trading_calendar.CalendarUnavailable:
                        session_dates = None
        rows = [row for row in load_and_calculate(snap_path, session_dates=session_dates) if start <= row['date'] <= end]
        if (not archived_calendar.is_file() and synthetic_non_sessions
                and (len(rows) < 2 or rows[0]['date'] != start or rows[-1]['date'] != end)):
            # Synthetic non-trading-day fixtures built without an exchange calendar.
            session_dates = None
            rows = [row for row in load_and_calculate(snap_path) if start <= row['date'] <= end]
        if len(rows) < 2 or rows[0]['date'] != start or rows[-1]['date'] != end:
            raise ValueError('换手率快照日期与旧图不一致，无法安全重绘')
        with tempfile.TemporaryDirectory(prefix='turnover-delivery-') as tmp:
            tmp_dir = Path(tmp)
            command = [
                sys.executable, str(BUILD_TURNOVER),
                '--data', str(snap_path.resolve()), '--start', start,
                '--as-of', end, '--out-dir', tmp,
            ]
            if archived_calendar.is_file():
                command.extend(['--calendar', str(archived_calendar.resolve())])
            elif session_dates is not None:
                calendar_file = tmp_dir / 'calendar.json'
                calendar_file.write_text(json.dumps({'sessions': session_dates}), encoding='utf-8')
                command.extend(['--calendar', str(calendar_file)])
            try:
                done = subprocess.run(command, capture_output=True, timeout=90)
            except subprocess.TimeoutExpired:
                raise ValueError('换手率历史快照重绘超时，请稍后重试') from None
            if done.returncode:
                raise ValueError('换手率历史快照重绘失败，未请求外部数据')
            rebuilt = next(tmp_dir.glob('换手率*.png'))
            original = _open_rgb(rebuilt, dark=dark)
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
    fonts = NoteFonts(font_size) if notes else None
    lines = wrapped_lines(notes, fonts, width - 2 * margin) if (notes and fonts is not None) else []
    line_height = round(font_size * 1.55)
    with Image.open(WATERMARK) as asset:
        logo = asset.convert('RGBA')
        logo_width = min(340, max(120, round(width * .23)), width - 2 * margin)
        logo = logo.resize((logo_width, round(logo.height * logo_width / logo.width)), Image.Resampling.LANCZOS)
    if reuse_footer:
        # Liquidity already reserves the right side of its plain footer for branding.
        # Always clear any pre-rendered watermark rect even when notes are appended below.
        ImageDraw.Draw(original).rectangle((round(width * .7), height - 180, width, height),
                                           fill=original.getpixel((0, 0)))
        if not notes:
            original.paste(logo, (width - margin - logo.width, height - margin - logo.height), logo)
            return original
    footer_height = (len(lines) * line_height + margin if lines else 0) + logo.height + 2 * margin
    result = Image.new('RGB', (width, height + footer_height), background)
    result.paste(original, (0, 0))
    if lines and fonts is not None:
        draw = ImageDraw.Draw(result)
        for index, line in enumerate(lines):
            fonts.draw(draw, (margin, height + margin + index * line_height), line,
                       '#D2D2D2' if dark else '#334155')
    result.paste(logo, (width - margin - logo.width, result.height - margin - logo.height), logo)
    return result


def encode_image(image):
    """Keep dimensions first; never export a low-quality or oversized result."""
    def png(value):
        buffer = io.BytesIO()
        value.save(buffer, format='PNG', optimize=True)
        return buffer.getvalue()

    def jpeg(value, quality):
        buffer = io.BytesIO()
        value.save(buffer, format='JPEG', quality=quality, subsampling=0,
                   optimize=True, progressive=True)
        return buffer.getvalue()

    # Check raw PNG first before spending CPU quantizing colors.
    raw = png(image)
    if len(raw) <= MAX_BYTES:
        return raw, '.png', 'image/png'
    quantized = image.quantize(colors=256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    indexed = png(quantized)
    if len(indexed) <= MAX_BYTES:
        return indexed, '.png', 'image/png'

    # Photographic/AI images: high-quality 4:4:4 JPEG, then modest size reductions.
    # Probe quality=88 first at each width so oversize widths skip 95/92/90 immediately.
    floor = min(image.width, 1280)
    width = image.width
    while True:
        scaled = image if width == image.width else image.resize(
            (width, round(image.height * width / image.width)), Image.Resampling.LANCZOS)
        lowest = jpeg(scaled, 88)
        if len(lowest) <= MAX_BYTES:
            for quality in (95, 92, 90):
                content = jpeg(scaled, quality)
                if len(content) <= MAX_BYTES:
                    return content, '.jpg', 'image/jpeg'
            return lowest, '.jpg', 'image/jpeg'
        if width == floor:
            raise ValueError('图片在保持清晰度的条件下仍超过266KB，请调整图片内容或排版后重新生成')
        width = max(floor, round(width * .9))


def store_image(source, destination, *, notes='', dark=False, remove_valuation_source=False, reuse_footer=False,
                turnover_snapshot=None):
    image = compose_image(source, notes=notes, dark=dark, remove_valuation_source=remove_valuation_source,
                          reuse_footer=reuse_footer, turnover_snapshot=turnover_snapshot)
    content, suffix, media_type = encode_image(image)
    destination = Path(destination).with_suffix(suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Atomic publication also tolerates two readers preparing the same version.
    import uuid
    # Keep the atomic temporary name short: appending another UUID to a
    # 64-character cache key can exceed Windows' path limit in a workspace.
    temporary = destination.with_name('.image-' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_bytes(content)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination, media_type
