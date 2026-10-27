"""Install a checksum-verified LibreOffice Russian dictionary for local development.

No source document is transmitted. Docker installs the distro's hunspell-ru.
Run: python -m scripts.install_language_dictionary
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'dictionaries'
BASE = 'https://raw.githubusercontent.com/LibreOffice/dictionaries/master/ru_RU/'
FILES = {
    'ru_RU.aff': '38ce7d4af78e211e9bafe4bf7e3d6a2c420591136cb738ec6648f8fdf6524cd7',
    'ru_RU.dic': 'f6047416a0204adbecf3a451b874ec8a97ee37e2cbc714466ef04d8dbcc0d6fc',
    'README_ru_RU.txt': None,
}


def main():
    payloads = {}
    for name, checksum in FILES.items():
        with urlopen(BASE + name, timeout=30) as response:
            value = response.read(8 * 1024 * 1024)
        if checksum and hashlib.sha256(value).hexdigest() != checksum:
            raise RuntimeError(f'{name}: upstream file changed; review its source and license before updating the pinned checksum.')
        payloads[name] = value
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, value in payloads.items():
        path = ROOT / name
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_bytes(value)
        temporary.replace(path)
    print('Installed Russian dictionary and original license:', ROOT)


if __name__ == '__main__':
    main()
