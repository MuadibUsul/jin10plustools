"""Prepare a Caddy candidate changing only the existing PLUS site's upstream block."""
import sys
from pathlib import Path

source = Path(sys.argv[1]).read_text(encoding='utf-8')
old = '''plus.tlines.tech {
    encode zstd gzip
    request_body {
        max_size 20MB
    }
    reverse_proxy plus-api:8000
}'''
new = '''plus.tlines.tech {
    encode zstd gzip
    request_body {
        max_size 20MB
    }
    redir /test /test/ 308
    handle_path /test/* {
        reverse_proxy plus-next-api:8000
    }
    handle {
        reverse_proxy plus-api:8000
    }
}'''
if source.count(old) != 1:
    raise SystemExit('PLUS route differs from the reviewed configuration; inspect before changing it')
Path(sys.argv[2]).write_text(source.replace(old, new), encoding='utf-8')
