"""Motore conversazionale di HARIA.

Costruisce il system prompt (identità + entità HA + memoria utente + data/ora),
espone i tool core (stato casa, controllo dispositivi, memoria, Alexa, recall,
respond) più quelli dei moduli abilitati, e gira il loop agentico con Claude
Haiku: il modello chiama i tool finché non produce una risposta via 'respond'.
Gestisce anche il riassunto automatico della history quando supera la finestra.
"""
import json
import logging
from datetime import datetime
import anthropic
from anthropic import RateLimitError
from ha_client import get_states, call_service
from memory import (
    get_history, save_turn, get_notes, save_note,
    get_entity_cache, save_entity_cache, clear_entity_cache,
    get_summary, set_summary, get_old_turns, delete_turns,
    count_history, search_memory,
    MAX_HISTORY, SUMMARY_BATCH,
)
import modules
import prompts
import config as cfg

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=cfg.get("anthropic_key"))
MODEL = "claude-haiku-4-5-20251001"

CORE_TOOLS = [
    {
        "name": "get_house_state",
        "description": "Scopri entità HA o leggi stato live. Senza entity_ids: restituisce lista cached (entity_id + nome) per trovare l'entity_id giusto. Con entity_ids: restituisce stato live di quelle entità.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista di entity_id HA (es. light.salotto). Vuoto = tutte.",
                }
            },
        },
    },
    {
        "name": "control_device",
        "description": "Controlla un dispositivo Home Assistant chiamando un servizio.",
        "input_schema": {
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Dominio HA (es. light, switch, climate)"},
                "service": {"type": "string", "description": "Servizio (es. turn_on, turn_off, set_temperature)"},
                "data": {
                    "type": "object",
                    "description": "Dati servizio (es. {entity_id: 'light.salotto', brightness: 128})",
                },
            },
            "required": ["domain", "service", "data"],
        },
    },
    {
        "name": "get_memory",
        "description": "Recupera note e preferenze salvate per l'utente corrente.",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "save_memory",
        "description": "Salva una nota o preferenza per l'utente corrente. Usa una chiave breve e descrittiva.",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Chiave breve (es. 'farmaco_mattina', 'medico_preferito')"},
                "value": {"type": "string", "description": "Valore da salvare"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "speak_alexa",
        "description": "Fai parlare ad alta voce un dispositivo Alexa/Echo (annuncio vocale TTS). Usa l'entity_id del media_player Echo (es. media_player.echo_show_cucina), che trovi nella lista entità.",
        "input_schema": {
            "type": "object",
            "properties": {
                "media_player": {"type": "string", "description": "entity_id del media_player Echo (es. media_player.echo_show_cucina)"},
                "message": {"type": "string", "description": "Testo da pronunciare"},
            },
            "required": ["media_player", "message"],
        },
    },
    {
        "name": "recall",
        "description": (
            "Cerca nella memoria a lungo termine (note salvate + conversazioni passate) "
            "per ricordare fatti vecchi non presenti nel contesto recente. Usa quando "
            "l'utente fa riferimento a qualcosa di detto tempo fa che non vedi nella cronologia."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Parole chiave da cercare (es. 'medico cardiologo', 'password wifi')"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "respond",
        "description": "Rispondi all'utente con un messaggio di testo. Usa questo tool per tutte le risposte.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Il testo della risposta per l'utente."},
            },
            "required": ["text"],
        },
    },
]

def get_tools() -> list[dict]:
    extra = modules.tools()
    if extra:
        # tool moduli prima del tool 'respond' finale
        return CORE_TOOLS[:-1] + extra + [CORE_TOOLS[-1]]
    return list(CORE_TOOLS)


async def refresh_entity_cache() -> int:
    states = await get_states(None)
    slim = [
        {"entity_id": s["entity_id"], "name": s["attributes"].get("friendly_name", s["entity_id"])}
        for s in states
    ]
    await save_entity_cache(json.dumps(slim, ensure_ascii=False))
    logger.info("Entity cache aggiornata: %d entità", len(slim))
    return len(slim)


async def _run_tool(name: str, inputs: dict, user_id: str) -> str:
    logger.info("Tool call: %s inputs=%s", name, inputs)
    try:
        if name == "get_house_state":
            entity_ids = inputs.get("entity_ids") or None
            if entity_ids:
                # specific entities: return live state
                states = await get_states(entity_ids)
                return json.dumps(states, ensure_ascii=False)
            # no filter: return cached entity list (entity_id + name only, no live state)
            cached = await get_entity_cache()
            if cached:
                return cached
            await refresh_entity_cache()
            return await get_entity_cache()
        if name == "control_device":
            result = await call_service(inputs["domain"], inputs["service"], inputs["data"])
            return json.dumps(result, ensure_ascii=False)
        if name == "get_memory":
            notes = await get_notes(user_id)
            return json.dumps(notes, ensure_ascii=False) if notes else "Nessuna nota salvata."
        if name == "save_memory":
            await save_note(user_id, inputs["key"], inputs["value"])
            return f"Nota '{inputs['key']}' salvata."
        if name == "speak_alexa":
            mp = inputs["media_player"]
            object_id = mp.split(".", 1)[1] if "." in mp else mp
            notify_service = f"alexa_media_{object_id}"
            message = inputs["message"]
            last_error = None
            for announce_type in ("announce", "tts"):
                try:
                    await call_service("notify", notify_service, {
                        "message": message,
                        "data": {"type": announce_type},
                    })
                    return json.dumps(
                        {"ok": True, "mode": announce_type, "device": mp},
                        ensure_ascii=False,
                    )
                except Exception as e:
                    logger.warning("speak_alexa %s su %s fallito: %s", announce_type, mp, e)
                    last_error = e
            return json.dumps(
                {"ok": False, "error": str(last_error), "device": mp},
                ensure_ascii=False,
            )
        if name == "recall":
            hits = await search_memory(user_id, inputs.get("query", ""), limit=5)
            return json.dumps(hits, ensure_ascii=False) if hits else "Nessun ricordo trovato."
        if name == "respond":
            return "__respond__"
        # tool dei moduli abilitati (reminders, web_search, ...)
        if modules.owns(name):
            return await modules.dispatch(name, inputs, user_id)
        return f"Tool sconosciuto: {name}"
    except (ConnectionError, PermissionError, TimeoutError) as e:
        logger.warning("Tool %s fallito: %s", name, e)
        return f"Errore: {e}"
    except Exception as e:
        logger.error("Tool %s errore inatteso: %s", name, e)
        return f"Errore nel tool {name}: {e}"


async def _build_system(user_id: str, user_config: dict) -> list[dict]:
    name = user_config.get("name", "Utente")
    context = user_config.get("context", "")
    base = prompts.get("system_base", name=name)
    base += modules.prompt()

    cached = await get_entity_cache()
    if not cached:
        await refresh_entity_cache()
        cached = await get_entity_cache()
    if cached:
        base += f"\n\nENTITÀ DISPONIBILI (entity_id | nome):\n{cached}"
    if context:
        base += f"\n\nContesto utente: {context}"

    # Memoria a lungo termine: note salvate (auto-recall) + riassunto conversazioni vecchie.
    notes = await get_notes(user_id)
    if notes:
        note_lines = "\n".join(f"- {k}: {v}" for k, v in notes.items())
        base += ("\n\nMEMORIA UTENTE (note salvate, usale senza chiamare get_memory):\n"
                 + note_lines)
    summary = await get_summary(user_id)
    if summary:
        base += ("\n\nRIASSUNTO CONVERSAZIONI PRECEDENTI (contesto storico, non più nei messaggi):\n"
                 + summary)

    # Stable prefix cached; volatile datetime in separate uncached block.
    from datetime import timedelta
    now = datetime.now()
    _gg = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]
    monday = now.date() - timedelta(days=now.weekday())
    week_map = "; ".join(
        f"{_gg[i]}={(monday + timedelta(days=i)).isoformat()}" for i in range(7)
    )
    next_monday = monday + timedelta(days=7)
    week_map_next = "; ".join(
        f"{_gg[i]}={(next_monday + timedelta(days=i)).isoformat()}" for i in range(7)
    )
    dt_block = prompts.get(
        "datetime_block",
        now=now.isoformat(timespec="seconds"),
        weekday=_gg[now.weekday()],
        week_map=week_map,
        week_map_next=week_map_next,
    )
    return [
        {"type": "text", "text": base, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dt_block},
    ]


async def _maybe_summarize(user_id: str):
    """Se la history supera la finestra recente + batch, piega i turni vecchi in un
    riassunto (1 chiamata Haiku) e li rimuove dalla history raw. Best-effort."""
    try:
        if await count_history(user_id) <= MAX_HISTORY + SUMMARY_BATCH:
            return
        old = await get_old_turns(user_id)
        if not old:
            return
        prev = await get_summary(user_id)
        convo = "\n".join(f"{t['role']}: {t['content']}" for t in old)
        sys = prompts.get("summarize_system")
        prev_block = f"Riassunto esistente:\n{prev}\n\n" if prev else ""
        user_msg = prompts.get("summarize_user", prev_block=prev_block, convo=convo)
        resp = await client.messages.create(
            model=MODEL, max_tokens=400,
            system=sys,
            messages=[{"role": "user", "content": user_msg}],
        )
        new_summary = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
        if new_summary:
            await set_summary(user_id, new_summary)
            await delete_turns([t["id"] for t in old])
            logger.info("Summary memoria aggiornato per %s (%d turni piegati)", user_id, len(old))
    except Exception as e:
        logger.warning("Summarize memoria fallito per %s: %s", user_id, e)


async def chat(user_id: str, user_text: str, user_config: dict,
               image_b64: str | None = None, image_media_type: str = "image/jpeg",
               doc_b64: str | None = None, doc_media_type: str = "application/pdf") -> str:
    await _maybe_summarize(user_id)
    history = await get_history(user_id)
    placeholder = "[foto]" if image_b64 else ("[pdf]" if doc_b64 else None)
    await save_turn(user_id, "user", user_text or placeholder or "")

    if doc_b64:
        content = [
            {"type": "document", "source": {
                "type": "base64", "media_type": doc_media_type, "data": doc_b64}},
            {"type": "text", "text": user_text or prompts.get("doc_classify")},
        ]
        messages = history + [{"role": "user", "content": content}]
    elif image_b64:
        content = [
            {"type": "image", "source": {
                "type": "base64", "media_type": image_media_type, "data": image_b64}},
            {"type": "text", "text": user_text or prompts.get("image_meal")},
        ]
        messages = history + [{"role": "user", "content": content}]
    else:
        messages = history + [{"role": "user", "content": user_text}]
    system = await _build_system(user_id, user_config)

    MAX_TURNS = 8
    try:
        for turn in range(MAX_TURNS):
            # ultimo giro: forza una risposta testuale (evita loop infinito di tool)
            force_respond = turn == MAX_TURNS - 1
            response = await client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system,
                tools=get_tools(),
                tool_choice=({"type": "tool", "name": "respond"} if force_respond
                             else {"type": "any"}),
                messages=messages,
            )

            tool_results = []
            reply = None

            for block in response.content:
                if block.type == "tool_use":
                    if block.name == "respond":
                        reply = block.input.get("text", "")
                    else:
                        result = await _run_tool(block.name, block.input, user_id)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

            if reply is not None:
                await save_turn(user_id, "assistant", reply)
                return reply

            if tool_results:
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
                continue

            # fallback: estrai testo se presente
            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            reply = "\n".join(text_blocks)
            await save_turn(user_id, "assistant", reply)
            return reply

        # loop esaurito senza risposta (non dovrebbe capitare: ultimo giro forza respond)
        logger.warning("chat: raggiunto MAX_TURNS senza respond per user %s", user_id)
        fallback = "Ho avuto un problema a completare la richiesta. Riprova."
        await save_turn(user_id, "assistant", fallback)
        return fallback

    except RateLimitError:
        logger.warning("Rate limit Anthropic raggiunto per user %s", user_id)
        return "⚠️ Troppe richieste in poco tempo. Riprova tra un minuto."
    except anthropic.APIError as e:
        logger.error("Errore API Anthropic: %s", e)
        return "Errore di comunicazione con l'AI. Riprova."
