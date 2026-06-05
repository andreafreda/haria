"""Modulo news: briefing notizie configurabile (temi + orario cron dinamici).

L'utente definisce via chat temi/keyword e un orario (cron). Ogni schedulazione
HARIA cerca sul web per ogni tema, riassume con Haiku e invia il briefing.
"""
import json
import logging
import web_search
import scheduler
from memory import (
    add_briefing, get_user_briefings, get_briefing,
    deactivate_briefing, update_briefing,
)

NAME = "news"

logger = logging.getLogger(__name__)

_RESULTS_PER_TOPIC = 5


async def generate(topics: str) -> str:
    """Cerca notizie per ogni tema e produce un breve briefing riassunto."""
    topic_list = [t.strip() for t in (topics or "").split(",") if t.strip()]
    if not topic_list:
        return "Nessun tema configurato per il briefing."
    blocks: list[str] = []
    for t in topic_list:
        results = await web_search.search(f"{t} notizie ultime", _RESULTS_PER_TOPIC)
        if not results:
            continue
        lines = [f"# Tema: {t}"]
        for r in results:
            title = r.get("title") or ""
            snippet = r.get("body") or r.get("snippet") or ""
            url = r.get("href") or r.get("url") or ""
            lines.append(f"- {title}: {snippet} ({url})")
        blocks.append("\n".join(lines))
    if not blocks:
        return "Nessuna notizia trovata per i temi configurati."

    raw = "\n\n".join(blocks)
    from claude_engine import client, MODEL
    prompt = (
        "Sei un assistente che prepara un briefing mattutino di notizie. "
        "Dai risultati di ricerca qui sotto, scrivi un riassunto BREVE e chiaro in italiano, "
        "raggruppato per tema. Per ogni tema 2-4 punti sintetici sulle notizie principali. "
        "Niente preamboli, vai dritto al briefing. Mantieni i link delle fonti tra parentesi "
        "solo se rilevanti.\n\n"
        f"RISULTATI:\n{raw}"
    )
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
            "Crea un briefing notizie ricorrente per l'utente. Cerca sul web i temi indicati e "
            "manda un riassunto agli orari definiti dal cron. Es: temi 'intelligenza artificiale, "
            "Inter, mercati', cron '30 7 * * *' (ogni giorno 7:30)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topics": {"type": "string", "description": "Temi/keyword separati da virgola"},
                "cron": {"type": "string", "description": "Espressione cron standard (min ora giorno mese giorno-settimana)"},
            },
            "required": ["topics", "cron"],
        },
    },
    {
        "name": "list_briefings",
        "description": "Elenca i briefing notizie configurati dall'utente (id, temi, cron).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_briefing",
        "description": "Modifica un briefing esistente dato il suo id. Passa topics e/o cron.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "topics": {"type": "string"},
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
]

PROMPT = (
    "\n- BRIEFING NOTIZIE: l'utente può chiedere riassunti notizie ricorrenti su temi a sua scelta "
    "(create_briefing con topics + cron). Converti tu l'orario richiesto in espressione cron. "
    "Usa list_briefings/update_briefing/delete_briefing per gestirli."
)


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name == "create_briefing":
        b = await add_briefing(user_id, inputs["topics"], inputs["cron"])
        ok = scheduler.schedule_briefing(b)
        if not ok:
            await deactivate_briefing(b["id"], user_id)
            return json.dumps({"ok": False, "error": "Cron non valido"}, ensure_ascii=False)
        return json.dumps({"ok": True, "briefing": b}, ensure_ascii=False)

    if name == "list_briefings":
        items = await get_user_briefings(user_id)
        return json.dumps(items, ensure_ascii=False) if items else "Nessun briefing configurato."

    if name == "update_briefing":
        b = await update_briefing(int(inputs["id"]), user_id,
                                  inputs.get("topics"), inputs.get("cron"))
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

    return f"Tool sconosciuto nel modulo {NAME}: {name}"
