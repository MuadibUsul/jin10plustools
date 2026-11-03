"""Validate deployed distributions without exposing environment or keys."""
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path

def check(path):
    mismatches=[]
    for raw in Path(path).read_text(encoding='utf-8-sig').splitlines():
        line=raw.strip()
        if not line or line.startswith('#'):continue
        name,expected=line.split('==',1)
        try:actual=version(name)
        except PackageNotFoundError:actual='missing'
        if actual!=expected:mismatches.append(name+': expected '+expected+', installed '+actual)
    if mismatches:raise RuntimeError('Runtime lock mismatch: '+'; '.join(mismatches))

if __name__=='__main__':
    import sys
    check(sys.argv[1]);print('Validated runtime lock')
