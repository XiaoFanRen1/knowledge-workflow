"""Canonical ZIP metadata for a source-built wheel; member contents stay unchanged."""
import argparse
import hashlib
import os
import zipfile
from pathlib import Path


def normalize(path):
    temporary = path.with_suffix(".normalized")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for name in sorted(source.namelist()):
            if name.endswith("/"):
                continue
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            target.writestr(info, source.read(name), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    os.replace(temporary, path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()
    print(normalize(args.wheel))
