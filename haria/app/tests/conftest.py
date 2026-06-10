import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory  # noqa: E402


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Inizializza un DB HARIA temporaneo e isolato per il test."""
    monkeypatch.setattr(memory, "DB_PATH", str(tmp_path / "haria_test.db"))
    await memory.init_db()
    return memory
