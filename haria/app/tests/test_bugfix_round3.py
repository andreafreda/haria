"""Bugfix round 3 (review 2026-06-12) — TASK 27-34."""
import os

import pytest

import scheduler
import errorlog
import claude_engine
from modules import food_diary


# ---- TASK 27: reminder corrotto non bricka ----

def test_schedule_one_remind_at_non_iso():
    # remind_at non-ISO + recurring None -> False, niente eccezione
    r = {"id": 1, "user_id": "u", "message": "m", "recurring": None, "remind_at": "domani"}
    assert scheduler._schedule_one(r) is False


def test_schedule_one_remind_at_none():
    r = {"id": 2, "user_id": "u", "message": "m", "recurring": None, "remind_at": None}
    assert scheduler._schedule_one(r) is False


# ---- TASK 29: _conv_dow gestisce gli step ----

def test_conv_dow_step():
    assert scheduler._conv_dow("*/2") == "*/2"


def test_conv_dow_range_list_single():
    assert scheduler._conv_dow("1-5") == "mon-fri"
    assert scheduler._conv_dow("0,6") == "sun,sat"
    assert scheduler._conv_dow("5") == "fri"
    assert scheduler._conv_dow("*") == "*"


def test_cron_trigger_step_dow_no_raise():
    scheduler._cron_trigger("0 8 * * */2")  # non solleva


# ---- TASK 30: throttle notifiche push ----

async def test_dispatch_throttle(monkeypatch):
    errorlog._last_notify.clear()
    logged = []
    pushed = []

    async def _fake_add(*a):
        logged.append(a)

    async def _fake_call(dom, svc, data):
        pushed.append((dom, svc, data))

    monkeypatch.setattr(errorlog, "add_error_log", _fake_add)
    monkeypatch.setattr(errorlog, "call_service", _fake_call)
    monkeypatch.setattr(errorlog, "_notify_target", lambda: ("notify", "x"))

    await errorlog._dispatch("src", "boom uguale", "")
    await errorlog._dispatch("src", "boom uguale", "")

    assert len(logged) == 2   # log su DB sempre
    assert len(pushed) == 1   # push throttlata


# ---- TASK 31: escape wildcard FTS in save_note ----

async def test_save_note_fts_escape(db):
    await db.save_note("u", "a_b", "primo")
    await db.save_note("u", "axb", "secondo")
    # update di a_b non deve cancellare la riga FTS di axb
    await db.save_note("u", "a_b", "primo-mod")
    hits = await db.search_memory("u", "secondo")
    assert any("secondo" in h for h in hits)


# ---- TASK 32: rename/merge propagano su econ_regole ----

async def test_rename_categoria_propaga_regole(db):
    await db.add_regola("xyzmarket", "svago")
    assert await db.rename_categoria("svago", "divertimento") is True
    regole = await db.list_regole()
    cat = [r["categoria"] for r in regole if r["keyword"] == "xyzmarket"][0]
    assert cat == "divertimento"


async def test_merge_categoria_propaga_regole(db):
    await db.add_regola("xyzmarket", "svago")
    await db.normalize_categoria("tempo libero")
    assert await db.merge_categoria("svago", "tempo libero") is True
    regole = await db.list_regole()
    cat = [r["categoria"] for r in regole if r["keyword"] == "xyzmarket"][0]
    assert cat == "tempo libero"


# ---- TASK 33: cache diete su mtime ----

def test_load_diets_cache(tmp_path, monkeypatch):
    d = tmp_path / "diets"
    d.mkdir()
    (d / "a.md").write_text("dieta uno", encoding="utf-8")
    monkeypatch.setattr(food_diary, "_DIET_DIRS", [str(d)])
    food_diary._diets_cache = None
    r1 = food_diary.load_diets()
    assert "dieta uno" in r1
    # seconda chiamata = stessa cache
    assert food_diary.load_diets() == r1
    # modifica file -> mtime cambia -> contenuto aggiornato
    os.utime(d / "a.md", None)
    (d / "a.md").write_text("dieta due", encoding="utf-8")
    import time
    os.utime(d / "a.md", (time.time() + 5, time.time() + 5))
    assert "dieta due" in food_diary.load_diets()


# ---- TASK 28 + 34: HA giù non blocca; APIError salva turno assistant ----

class _FakeAPIError(claude_engine.anthropic.APIError):
    def __init__(self):
        pass

    def __str__(self):
        return "fake api error"


async def test_chat_apierror_salva_turno_assistant(db, monkeypatch):
    monkeypatch.setattr(claude_engine, "refresh_entity_cache",
                        _raise_conn)
    monkeypatch.setattr(claude_engine, "get_entity_cache", _empty)

    class _Msgs:
        async def create(self, **kw):
            raise _FakeAPIError()

    class _Client:
        messages = _Msgs()

    monkeypatch.setattr(claude_engine, "client", _Client())

    out = await claude_engine.chat("123", "ciao", {"name": "Test"})
    assert "Errore di comunicazione" in out
    hist = await db.get_history("123")
    assert hist[-1]["role"] == "assistant"


async def _raise_conn():
    raise ConnectionError("HA giù")


async def _empty():
    return ""


async def test_build_system_ha_giu_no_eccezione(db, monkeypatch):
    monkeypatch.setattr(claude_engine, "refresh_entity_cache", _raise_conn)
    monkeypatch.setattr(claude_engine, "get_entity_cache", _empty)
    sys_blocks = await claude_engine._build_system("123", {"name": "Test"})
    txt = sys_blocks[0]["text"]
    assert "non disponibili" in txt
