"""Human-facing copy dates; source timestamps, URLs and data keys stay unchanged."""
import re
from datetime import date


def copy_dates(text):
    def replace(match):
        year, month, day, hour, minute = match.groups()
        try:
            date(int(year), int(month), int(day))
        except ValueError:
            return match[0]
        result = f'{year}年{int(month)}月{int(day)}日'
        return result + (f' {int(hour)}:{minute}' if hour is not None else '')
    # Never rewrite source URLs embedded in explanatory copy.
    return ''.join(part if re.match(r'https?://', part) else re.sub(
        r'(?<![\d/])(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{1,2}):(\d{2}))?', replace, part)
        for part in re.split(r'(https?://[^\s<>]+)', text))
