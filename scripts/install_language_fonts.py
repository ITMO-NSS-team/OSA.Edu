"""Install the mathematical fallback font locally, without changing system fonts."""
from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path
from urllib.request import urlopen

URL = 'https://deb.debian.org/debian/pool/main/f/fonts-dejavu/fonts-dejavu-extra_2.37-6_all.deb'
SHA256 = '6cb7ed32de6df209c065e9e5cc5154a2f36d4c334d9a0af534fabb67d38ab75b'


def main():
    with urlopen(URL, timeout=30) as response:
        package = response.read(4 * 1024 * 1024)
    if hashlib.sha256(package).hexdigest() != SHA256 or package[:8] != b'!<arch>\n':
        raise RuntimeError('The Debian font package failed checksum/format validation.')
    offset = 8
    while offset + 60 <= len(package):
        header = package[offset:offset + 60]
        name = header[:16].decode().strip().rstrip('/')
        size = int(header[48:58])
        offset += 60
        payload = package[offset:offset + size]
        offset += size + size % 2
        if not name.startswith('data.tar'):
            continue
        root = Path(__file__).resolve().parents[1] / 'data' / 'fonts'
        root.mkdir(parents=True, exist_ok=True)
        wanted = {'./usr/share/fonts/truetype/dejavu/DejaVuMathTeXGyre.ttf': 'DejaVuMathTeXGyre.ttf',
                  './usr/share/doc/fonts-dejavu-extra/copyright': 'LICENSE-DejaVu.txt'}
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:*') as archive:
            for member, filename in wanted.items():
                with archive.extractfile(member) as stream:
                    value = stream.read()
                (root / filename).write_bytes(value)
        print('Installed mathematical font and original license:', root)
        return
    raise RuntimeError('No data archive in the verified Debian package.')


if __name__ == '__main__':
    main()
