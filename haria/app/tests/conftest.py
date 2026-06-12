import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import memory  # noqa: E402


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Inizializza un DB HARIA temporaneo e isolato per il test."""
    # DB_PATH canonico vive in memory.core (i sottomoduli lo leggono da lì a
    # runtime); il vecchio monkeypatch su memory.DB_PATH non raggiungerebbe i
    # sottomoduli dopo lo split in package (TASK 42).
    monkeypatch.setattr(memory.core, "DB_PATH", str(tmp_path / "haria_test.db"))
    await memory.init_db()
    return memory
