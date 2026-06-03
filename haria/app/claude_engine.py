import json
import logging
import anthropic
from anthropic import RateLimitError
from ha_client import get_states, call_service
from memory import get_history, save_turn, get_notes, save_note, get_entity_cache, save_entity_cache, clear_entity_cache
import config as cfg

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=cfg.get("anthropic_key"))
MODEL = "claude-haiku-4-5-20251001"

TOOLS = [
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
        if name == "respond":
            return "__respond__"
        return f"Tool sconosciuto: {name}"
    except (ConnectionError, PermissionError, TimeoutError) as e:
        logger.warning("Tool %s fallito: %s", name, e)
        return f"Errore: {e}"
    except Exception as e:
        logger.error("Tool %s errore inatteso: %s", name, e)
        return f"Errore imprevisto nel tool {name}."


def _build_system(user_config: dict) -> str:
    name = user_config.get("name", "Utente")
    context = user_config.get("context", "")
    base = (
        f"Sei HARIA, assistente AI personale di {name}. "
        "Sei integrata in Home Assistant e controlli la casa tramite i tool disponibili.\n\n"
        "REGOLE OBBLIGATORIE:\n"
        "- Devi SEMPRE usare il tool 'respond' per rispondere all'utente. Non puoi rispondere con testo libero.\n"
        "- Se l'utente chiede di controllare qualcosa in casa, chiama PRIMA control_device (o get_house_state se non conosci l'entity_id), POI respond.\n"
        "- NON chiedere MAI all'utente l'entity_id. Usa get_house_state (entity_ids vuoto) per scoprirlo.\n"
        "- Per luci usa domain='light', service='turn_on' o 'turn_off', data={'entity_id': '...'}.\n"
        "- Per switch usa domain='switch'.\n"
        "- Rispondi in italiano, in modo conciso."
    )
    if context:
        base += f"\n\nContesto utente: {context}"
    return base


async def chat(user_id: str, user_text: str, user_config: dict) -> str:
    history = await get_history(user_id)
    await save_turn(user_id, "user", user_text)

    messages = history + [{"role": "user", "content": user_text}]
    system = _build_system(user_config)

    try:
        while True:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system,
                tools=TOOLS,
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
