import json
import logging
from datetime import datetime
import anthropic
from anthropic import RateLimitError
from ha_client import get_states, call_service
from memory import (
    get_history, save_turn, get_notes, save_note,
    get_entity_cache, save_entity_cache, clear_entity_cache,
    add_reminder, get_user_reminders, deactivate_reminder,
)
import scheduler
import web_search
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

REMINDER_TOOLS = [
    {
        "name": "set_reminder",
        "description": "Crea un promemoria. Usa remind_at (ISO datetime, es. '2026-06-03T18:30:00') per one-shot, OPPURE recurring (espressione cron a 5 campi 'min hour day month dow', es. '0 9 * * 1' = ogni lunedì 9:00) per ricorrenti. Non entrambi.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Testo del promemoria"},
                "remind_at": {"type": "string", "description": "ISO datetime per promemoria one-shot"},
                "recurring": {"type": "string", "description": "Espressione cron per promemoria ricorrenti"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Elenca i promemoria attivi dell'utente corrente.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancella un promemoria tramite il suo id (ottenuto da list_reminders).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "ID del promemoria"}},
            "required": ["id"],
        },
    },
]

WEB_SEARCH_TOOLS = [
    {
        "name": "search_web",
        "description": "Cerca informazioni aggiornate sul web (notizie, fatti recenti, dati che non conosci). Restituisce titolo, url e snippet dei risultati.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Query di ricerca"},
                "max_results": {"type": "integer", "description": "Numero risultati (default 5)"},
            },
            "required": ["query"],
        },
    },
]


def _module_enabled(name: str) -> bool:
    return bool(cfg.get("modules", {}).get(name, False))


def get_tools() -> list[dict]:
    extra = []
    if _module_enabled("reminders"):
        extra += REMINDER_TOOLS
    if _module_enabled("web_search"):
        extra += WEB_SEARCH_TOOLS
    if extra:
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
        if name == "set_reminder":
            remind_at = inputs.get("remind_at") or None
            recurring = inputs.get("recurring") or None
            if not remind_at and not recurring:
                return "Errore: specifica remind_at (one-shot) o recurring (cron)."
            r = await add_reminder(user_id, inputs["message"], remind_at, recurring)
            if not scheduler.schedule_reminder(r):
                await deactivate_reminder(r["id"], user_id)
                return "Errore: orario non valido o nel passato."
            return f"Promemoria #{r['id']} creato."
        if name == "list_reminders":
            rem = await get_user_reminders(user_id)
            return json.dumps(rem, ensure_ascii=False) if rem else "Nessun promemoria attivo."
        if name == "cancel_reminder":
            ok = await deactivate_reminder(int(inputs["id"]), user_id)
            if ok:
                scheduler.cancel_job(int(inputs["id"]))
                return f"Promemoria #{inputs['id']} cancellato."
            return f"Promemoria #{inputs['id']} non trovato."
        if name == "speak_alexa":
            mp = inputs["media_player"]
            object_id = mp.split(".", 1)[1] if "." in mp else mp
            notify_service = f"alexa_media_{object_id}"
            message = inputs["message"]
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
        if name == "search_web":
            results = await web_search.search(inputs["query"], inputs.get("max_results", 5))
            return json.dumps(results, ensure_ascii=False) if results else "Nessun risultato."
        if name == "respond":
            return "__respond__"
        return f"Tool sconosciuto: {name}"
    except (ConnectionError, PermissionError, TimeoutError) as e:
        logger.warning("Tool %s fallito: %s", name, e)
        return f"Errore: {e}"
    except Exception as e:
        logger.error("Tool %s errore inatteso: %s", name, e)
        return f"Errore imprevisto nel tool {name}."


async def _build_system(user_config: dict) -> list[dict]:
    name = user_config.get("name", "Utente")
    context = user_config.get("context", "")
    base = (
        f"Sei HARIA, assistente AI personale di {name}. "
        "Sei integrata in Home Assistant e controlli la casa tramite i tool disponibili.\n\n"
        "REGOLE OBBLIGATORIE:\n"
        "- Devi SEMPRE usare il tool 'respond' per rispondere all'utente. Non puoi rispondere con testo libero.\n"
        "- Hai già la lista completa delle entità qui sotto: usa direttamente l'entity_id giusto, NON chiamare get_house_state per scoprirlo.\n"
        "- Se l'utente chiede di controllare qualcosa, chiama control_device col giusto entity_id, POI respond.\n"
        "- Usa get_house_state SOLO se serve lo stato live di un'entità specifica (es. 'la luce è accesa?').\n"
        "- NON chiedere MAI all'utente l'entity_id.\n"
        "- Per luci usa domain='light', service='turn_on' o 'turn_off', data={'entity_id': '...'}.\n"
        "- Per switch usa domain='switch'.\n"
        "- Per far PARLARE ad alta voce un Echo/Alexa usa speak_alexa con l'entity_id del media_player (es. media_player.echo_show_cucina). NON usare control_device per gli annunci vocali.\n"
        "- Rispondi in italiano, in modo conciso."
    )
    if _module_enabled("reminders"):
        base += (
            "\n- Per promemoria usa set_reminder: calcola remind_at (ISO datetime) dalla data/ora attuale nel blocco volatile."
            " Per ricorrenti usa recurring (cron 5 campi). Usa list_reminders/cancel_reminder per gestirli."
        )
    if _module_enabled("web_search"):
        base += (
            "\n- Per informazioni aggiornate o che non conosci (notizie, eventi recenti, dati attuali) usa search_web, poi rispondi citando le fonti."
        )

    cached = await get_entity_cache()
    if not cached:
        await refresh_entity_cache()
        cached = await get_entity_cache()
    if cached:
        base += f"\n\nENTITÀ DISPONIBILI (entity_id | nome):\n{cached}"
    if context:
        base += f"\n\nContesto utente: {context}"

    # Stable prefix cached; volatile datetime in separate uncached block.
    return [
        {"type": "text", "text": base, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": f"Data/ora attuale: {datetime.now().isoformat(timespec='seconds')}"},
    ]


async def chat(user_id: str, user_text: str, user_config: dict) -> str:
    history = await get_history(user_id)
    await save_turn(user_id, "user", user_text)

    messages = history + [{"role": "user", "content": user_text}]
    system = await _build_system(user_config)

    try:
        while True:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system,
                tools=get_tools(),
                tool_choice={"type": "any"},
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

    except RateLimitError:
        logger.warning("Rate limit Anthropic raggiunto per user %s", user_id)
        return "⚠️ Troppe richieste in poco tempo. Riprova tra un minuto."
    except anthropic.APIError as e:
        logger.error("Errore API Anthropic: %s", e)
        return "Errore di comunicazione con l'AI. Riprova."
