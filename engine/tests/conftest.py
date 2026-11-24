import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from reinman.db import SQLiteDatabase  # noqa: E402
from reinman.registry import ensure_baseline  # noqa: E402


@pytest.fixture
def db():
    d = SQLiteDatabase(":memory:")
    d.migrate()
    ensure_baseline(d)
    return d
