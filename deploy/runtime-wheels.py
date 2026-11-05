"""Download only changed, locked distributions using the deployment's Linux ABI."""
from importlib.metadata import PackageNotFoundError,version
from pathlib import Path
import subprocess,sys,tomllib


def main():
    lock=Path(sys.argv[1]);destination=Path(sys.argv[2]);destination.mkdir(parents=True,exist_ok=True)
    changed=list(tomllib.loads(lock.with_name('pyproject.toml').read_text(encoding='utf-8'))['build-system']['requires'])
    for line in lock.read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#'):continue
        name,wanted=line.split('==',1)
        try:installed=version(name)
        except PackageNotFoundError:installed=None
        if installed!=wanted:changed.append(line)
    if changed:
        subprocess.run([sys.executable,'-m','pip','download','--only-binary=:all:','--no-deps',
                        '--dest',str(destination),*changed],check=True)
    print('Prepared changed runtime distributions:',len(changed))


if __name__=='__main__':main()
