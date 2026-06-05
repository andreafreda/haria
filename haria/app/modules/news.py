"""Modulo news: briefing notizie configurabile (temi + orario cron dinamici).

L'utente definisce via chat temi/keyword e un orario (cron). Ogni schedulazione
HARIA cerca sul web per ogni tema, riassume con Haiku e invia il briefing.

Filtri fonte:
  - whitelist PER-TEMA: un tema può limitare la ricerca a certi siti (site:).
  - blacklist GLOBALE per-utente: siti esclusi da tutti i temi (-site:).

`topics` è salvato come JSON: [{"topic": "ia", "sources": ["wired.it"]}, {"topic": "borsa"}].
Per retrocompatibilità accetta anche una semplice stringa con temi separati da virgola.
"""
import json
import logging
import web_search
import scheduler
import prompts
from memory import (
    add_briefing, get_user_briefings, get_briefing,
    deactivate_briefing, update_briefing,
    add_news_block, remove_news_block, get_news_blocks,
)

NAME = "news"

logger = logging.getLogger(__name__)

_RESULTS_PER_TOPIC = 5


def _parse_topics(topics: str) -> list[dict]:
    """Normalizza il campo topics in lista di {topic, sources}."""
    if not topics:
        return []
    try:
        data = json.loads(topics)
        if isinstance(data, list):
            out = []
            for it in data:
                if isinstance(it, dict) and it.get("topic"):
                    out.append({"topic": str(it["topic"]).strip(),
                                "sources": [s.strip().lower() for s in it.get("sources", []) if s.strip()]})
                elif isinstance(it, str) and it.strip():
                    out.append({"topic": it.strip(), "sources": []})
            return out
    except (ValueError, TypeError):
        pass
    # fallback: stringa "a, b, c"
    return [{"topic": t.strip(), "sources": []} for t in topics.split(",") if t.strip()]


def _build_query(topic: str, sources: list[str], blocks: list[str]) -> str:
    q = f"{topic} notizie ultime"
    if sources:
        q += " (" + " OR ".join(f"site:{s}" for s in sources) + ")"
    for b in blocks:
        q += f" -site:{b}"
    return q


async def generate(topics: str, user_id: str = "") -> str:
    """Cerca notizie per ogni tema (con filtri fonte) e produce un breve briefing."""
    items = _parse_topics(topics)
    if not items:
        return "Nessun tema configurato per il briefing."
    blocks = await get_news_blocks(user_id) if user_id else []
    blocks_out: list[str] = []
    for it in items:
        query = _build_query(it["topic"], it.get("sources", []), blocks)
        results = await web_search.search(query, _RESULTS_PER_TOPIC)
        if not results:
            continue
        lines = [f"# Tema: {it['topic']}"]
        for r in results:
            title = r.get("title") or ""
            snippet = r.get("snippet") or r.get("body") or ""
            url = r.get("url") or r.get("href") or ""
            lines.append(f"- {title}: {snippet} ({url})")
        blocks_out.append("\n".join(lines))
    if not blocks_out:
        return "Nessuna notizia trovata per i temi configurati."

    raw = "\n\n".join(blocks_out)
    from claude_engine import client, MODEL
    prompt = prompts.get("news_briefing", raw=raw)
    resp = await client.messages.create(
        model=MODEL, max_tokens=900,
        messages=[{"role": "user", "content": prompt}],
    )
    summary = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    return f"📰 Briefing notizie\n\n{summary}" if summary else raw


TOOLS = [
    {
        "name": "create_briefing",
        "description": (
            "Crea un briefing notizie ricorrente. Cerca sul web i temi indicati e manda un riassunto "
            "agli orari del cron. Ogni tema può opzionalmente limitare la ricerca a certi siti (whitelist "
            "PER-TEMA). Es: tema 'IA' aperto, tema 'calcio' solo da gazzetta.it. Converti tu l'orario in cron."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "description": "Lista temi. Ogni tema ha un nome e, se l'utente lo chiede, una whitelist di siti.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string", "description": "Tema/keyword"},
                            "sources": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Opzionale. Domini ammessi SOLO per questo tema (es. ['wired.it']). Vuoto = ricerca aperta.",
                            },
                        },
                        "required": ["topic"],
                    },
                },
                "cron": {"type": "string", "description": "Espressione cron standard (min ora giorno mese giorno-settimana)"},
            },
            "required": ["topics", "cron"],
        },
    },
    {
        "name": "list_briefings",
        "description": "Elenca i briefing notizie configurati dall'utente (id, temi, fonti, cron).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_briefing",
        "description": (
            "Modifica un briefing dato il suo id. Passa topics (lista completa aggiornata, sostituisce "
            "quella esistente) e/o cron. Per rimuovere un tema, rimanda topics senza quel tema."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "sources": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["topic"],
                    },
                },
                "cron": {"type": "string"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "delete_briefing",
        "description": "Cancella un briefing notizie dato il suo id.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "block_news_source",
        "description": (
            "Blocca un sito/dominio da TUTTE le notizie dell'utente (blacklist globale, permanente). "
            "Es. 'non darmi mai più notizie da abc.xy'. Passa il dominio (es. 'abc.xy')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"domain": {"type": "string", "description": "Dominio da bloccare (es. example.com)"}},
            "required": ["domain"],
        },
    },
    {
        "name": "unblock_news_source",
        "description": "Rimuove un dominio dalla blacklist globale notizie dell'utente.",
        "input_schema": {
            "type": "object",
            "properties": {"domain": {"type": "string"}},
            "required": ["domain"],
        },
    },
    {
        "name": "list_news_sources",
        "description": "Elenca i domini nella blacklist globale notizie dell'utente.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

PROMPT = prompts.get("module_news")


def _dump_topics(topics) -> str:
    """Serializza i topics ricevuti dal tool in JSON normalizzato."""
    norm = []
    for it in topics or []:
        if isinstance(it, dict) and it.get("topic"):
            norm.append({"topic": str(it["topic"]).strip(),
                         "sources": [s.strip().lower() for s in it.get("sources", []) if s.strip()]})
        elif isinstance(it, str) and it.strip():
            norm.append({"topic": it.strip(), "sources": []})
    return json.dumps(norm, ensure_ascii=False)


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name == "create_briefing":
        topics_json = _dump_topics(inputs["topics"])
        b = await add_briefing(user_id, topics_json, inputs["cron"])
        ok = scheduler.schedule_briefing(b)
        if not ok:
            await deactivate_briefing(b["id"], user_id)
            return json.dumps({"ok": False, "error": "Cron non valido"}, ensure_ascii=False)
        return json.dumps({"ok": True, "briefing": b}, ensure_ascii=False)

    if name == "list_briefings":
        items = await get_user_briefings(user_id)
        for it in items:
            it["topics"] = _parse_topics(it["topics"])
        return json.dumps(items, ensure_ascii=False) if items else "Nessun briefing configurato."

    if name == "update_briefing":
        topics_json = _dump_topics(inputs["topics"]) if "topics" in inputs else None
        b = await update_briefing(int(inputs["id"]), user_id, topics_json, inputs.get("cron"))
        if not b:
            return json.dumps({"ok": False, "error": "Briefing non trovato o nessun campo"}, ensure_ascii=False)
        scheduler.cancel_briefing(b["id"])
        ok = scheduler.schedule_briefing(b)
        return json.dumps({"ok": ok, "briefing": b}, ensure_ascii=False)

    if name == "delete_briefing":
        ok = await deactivate_briefing(int(inputs["id"]), user_id)
        if ok:
            scheduler.cancel_briefing(int(inputs["id"]))
        return json.dumps({"ok": ok, "id": inputs["id"]}, ensure_ascii=False)

    if name == "block_news_source":
        ok = await add_news_block(user_id, inputs["domain"])
        return json.dumps({"ok": ok, "domain": inputs.get("domain")}, ensure_ascii=False)

    if name == "unblock_news_source":
        ok = await remove_news_block(user_id, inputs["domain"])
        return json.dumps({"ok": ok, "domain": inputs.get("domain")}, ensure_ascii=False)

    if name == "list_news_sources":
        blocks = await get_news_blocks(user_id)
        return json.dumps({"blocklist": blocks}, ensure_ascii=False)

    return f"Tool sconosciuto nel modulo {NAME}: {name}"
