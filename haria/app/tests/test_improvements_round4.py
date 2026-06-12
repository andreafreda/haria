"""Round 4 migliorie — TASK 35-42."""
import claude_engine


# ---- TASK 36: prompt cache TTL 1h ----

def test_get_tools_cache_ttl_1h():
    assert claude_engine.get_tools()[-1]["cache_control"]["ttl"] == "1h"


async def _cache_x():
    return "x"


async def test_build_system_cache_ttl_1h(db, monkeypatch):
    monkeypatch.setattr(claude_engine, "get_entity_cache", _cache_x)
    blocks = await claude_engine._build_system("123", {"name": "Test"})
    assert blocks[0]["cache_control"]["ttl"] == "1h"
