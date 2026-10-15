"""Adapt an isolated test image's static pages to a proxy prefix; leave release sources intact."""
import re
import sys
from pathlib import Path


def prefix_web(folder: Path, prefix: str):
    if not re.fullmatch(r'/[a-zA-Z0-9_-]+', prefix):
        raise ValueError('Expected one URL path component, e.g. /test')
    for path in folder.rglob('*'):
        if path.suffix not in {'.html', '.js', '.css'}:
            continue
        source = path.read_text(encoding='utf-8')
        source = re.sub(r'''(["'`])/(?!/)''', lambda match: match[1] + prefix + '/', source)
        source = source.replace('plus.token', 'plus.test.token')
        if path.name == 'workspace.js':
            # File URLs in API responses remain relative to the backend root.
            source = source.replace('fetch(path,', f"fetch(path.startsWith('{prefix}/') ? path : '{prefix}' + path,")
        if path.name in {'portal.html', 'index.html', 'briefs.html'}:
            source = source.replace('<body>', '<body><div role="status" style="padding:10px;text-align:center;background:#fff4cc;color:#654700">独立测试站 · 数据为原站快照 · 操作不影响正式站</div>', 1)
        path.write_text(source, encoding='utf-8')


if __name__ == '__main__':
    prefix_web(Path(sys.argv[1]), sys.argv[2])
