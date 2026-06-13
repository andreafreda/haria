"""Ottimizzazione token: lista entità on-demand + tool_result cachato.

Verifica che:
  - il system prompt NON contenga più la lista entità (sta fuori, on-demand);
  - quando il modello chiama get_house_state senza entity_ids, il tool_result
    relativo riceva cache_control (ephemeral 1h) così la lista non viene
    rispedita per intero a ogni giro/messaggio.
"""
import json

import pytest

import claude_engine


class _Block:
    def __init__(self, name, bid, inp):
        self.type = "tool_use"
        self.name = name
        self.id = bid
        self.input = inp


class _Resp:
    def __init__(self, content):
        self.content = content
        self.usage = None


async def test_system_prompt_senza_lista_entita(db, monkeypatch):
    async def _cache():
        return json.dumps([{"entity_id": "light.x", "name": "X"}])
    monkeypatch.setattr(claude_engine, "get_entity_cache", _cache)
    sys_blocks = await claude_engine._build_system("123", {"name": "Test"})
    txt = sys_blocks[0]["text"]
    assert "ENTITÀ DISPONIBILI" not in txt
    assert "light.x" not in txt


async def test_note_e_summary_in_blocco_cache_separato(db, monkeypatch):
    """Note + summary NON devono stare nel blocco `base` (stabile, cachato): se ci
    stessero, ogni save_memory invaliderebbe l'intero prefisso. Devono avere un
    blocco cache_control proprio, distinto da base."""
    await db.save_note("123", "farmaco", "cardioaspirina ore 8")
    await db.set_summary("123", "L'utente prende farmaci la mattina.")
    blocks = await claude_engine._build_system("123", {"name": "Test"})

    # base (blocco 0) NON contiene la nota
    assert "cardioaspirina" not in blocks[0]["text"]
    # esiste un blocco cachato separato con la memoria utente
    mem = [b for b in blocks[1:] if "MEMORIA UTENTE" in b.get("text", "")]
    assert mem, "blocco memoria utente assente"
    assert mem[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}
    assert "cardioaspirina" in mem[0]["text"]
    assert "prende farmaci" in mem[0]["text"]  # summary nello stesso blocco
    # ultimo blocco = datetime, NON cachato (volatile ogni minuto)
    assert "cache_control" not in blocks[-1]


async def test_senza_note_nessun_blocco_memoria(db, monkeypatch):
    """Utente senza note/summary: niente blocco memoria vuoto (Anthropic rifiuta
    cache_control su testo vuoto)."""
    blocks = await claude_engine._build_system("999", {"name": "Test"})
    assert all("MEMORIA UTENTE" not in b.get("text", "") for b in blocks)
    assert all(b.get("text", "") != "" for b in blocks)


async def test_get_house_state_discovery_tool_result_cachato(db, monkeypatch):
    """Loop a 2 giri: discovery (get_house_state no entity_ids) poi respond.
    Il tool_result della discovery deve avere cache_control."""
    captured = {}

    async def _entity_cache():
        return json.dumps([{"entity_id": "light.salone", "name": "Salone"}])
    monkeypatch.setattr(claude_engine, "get_entity_cache", _entity_cache)

    calls = {"n": 0}

    class _Msgs:
        async def create(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp([_Block("get_house_state", "tu_1", {})])
            # 2° giro: cattura i messaggi ricevuti (contengono il tool_result)
            captured["messages"] = kw["messages"]
            return _Resp([_Block("respond", "tu_2", {"text": "fatto"})])

    class _Client:
        messages = _Msgs()

    monkeypatch.setattr(claude_engine, "client", _Client())

    out = await claude_engine.chat("123", "accendi il salone", {"name": "Test"})
    assert out == "fatto"

    # trova il blocco tool_result della discovery nei messaggi del 2° giro
    trs = [b for m in captured["messages"] if isinstance(m.get("content"), list)
           for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    disc = [b for b in trs if b["tool_use_id"] == "tu_1"]
    assert disc, "tool_result della discovery non trovato"
    assert disc[0].get("cache_control") == {"type": "ephemeral", "ttl": "1h"}


async def test_get_house_state_con_entity_ids_non_cachato(db, monkeypatch):
    """Con entity_ids (stato live, piccolo e variabile) NON va cachato."""
    async def _states(ids):
        return [{"entity_id": "light.salone", "state": "on", "attributes": {}}]
    monkeypatch.setattr(claude_engine, "get_states", _states)

    calls = {"n": 0}
    captured = {}

    class _Msgs:
        async def create(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _Resp([_Block("get_house_state", "tu_1", {"entity_ids": ["light.salone"]})])
            captured["messages"] = kw["messages"]
            return _Resp([_Block("respond", "tu_2", {"text": "accesa"})])

    class _Client:
        messages = _Msgs()

    monkeypatch.setattr(claude_engine, "client", _Client())
    out = await claude_engine.chat("123", "la luce del salone è accesa?", {"name": "Test"})
    assert out == "accesa"

    trs = [b for m in captured["messages"] if isinstance(m.get("content"), list)
           for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    live = [b for b in trs if b["tool_use_id"] == "tu_1"]
    assert live and "cache_control" not in live[0]
