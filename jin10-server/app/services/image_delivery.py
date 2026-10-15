"""Shared final-image contract for briefs and future fresh/annotation renderers."""
import hashlib
import io
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS = Path(__file__).parents[1]
WATERMARK = ASSETS / 'assets/plus-watermark.png'
FONT = ASSETS / 'briefs/assets/PlusBriefs.ttf'
MAX_BYTES = 266_000  # Also satisfies a 266 KiB limit.
POLICY_VERSION = '4'


def download_name(day, title, suffix):
    stamp = date.fromisoformat(str(day)).strftime('%Y%m%d')
    digest = hashlib.md5(title.encode('utf-8'), usedforsecurity=False).hexdigest()
    return f'{stamp}（{digest}）{suffix}'


def wrapped_lines(text, font, width):
    lines = []
    for paragraph in text.splitlines():
        line = ''
        for char in paragraph:
            if line and font.getlength(line + char) > width:
                lines.append(line)
                line = ''
            line += char
        lines.append(line)
    return lines


def compose_image(source, *, notes='', dark=False, remove_valuation_source=False, reuse_footer=False):
    """Use native pixels; add readable notes and an unobstructed original watermark."""
    with Image.open(source) as opened:
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
    font = ImageFont.truetype(str(FONT), font_size)
    lines = wrapped_lines(notes, font, width - 2 * margin) if notes else []
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
        draw.text((margin, height + margin + index * line_height), line, font=font,
                  fill='#D2D2D2' if dark else '#334155')
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


def store_image(source, destination, *, notes='', dark=False, remove_valuation_source=False, reuse_footer=False):
    image = compose_image(source, notes=notes, dark=dark, remove_valuation_source=remove_valuation_source,reuse_footer=reuse_footer)
    content, suffix, media_type = encode_image(image)
    destination = Path(destination).with_suffix(suffix)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Atomic publication also tolerates two readers preparing the same version.
    import uuid
    temporary = destination.with_name(destination.name + '.' + uuid.uuid4().hex + '.tmp')
    temporary.write_bytes(content)
    temporary.replace(destination)
    return destination, media_type
