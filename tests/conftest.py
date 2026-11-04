from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from p9.config import load_settings
from p9.ingest import Export, load_dir

DATA_DIR = load_settings(Path(__file__).parent).data_dir


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, raw in files.items():
            z.writestr(name, raw)
    return buf.getvalue()


def real_files() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(DATA_DIR.glob("*.csv"))}


@pytest.fixture(scope="session")
def export() -> Export:
    return load_dir(DATA_DIR)


@pytest.fixture(scope="session")
def export_zip() -> bytes:
    return zip_bytes({f"export/{n}": raw for n, raw in real_files().items()})
