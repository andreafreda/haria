import json
import logging
import os
import anthropic
from ha_client import get_states, call_service
from memory import get_history, save_turn, get_notes, save_note

logger = logging.getLogger(__name__)

client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-haiku-3-5-20241022"

TOOLS = [
    {
        "name": "get_house_state",
        "description": "Legge lo stato di entità da Home Assistant. Usa entity_ids per filtrare, lascia vuoto per tutte.",
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
]


async def _run_tool(name: str, inputs: dict, user_id: str) -> str:
    try:
        if name == "get_house_state":
            states = await get_states(inputs.get("entity_ids") or None)
            return json.dumps(states, ensure_ascii=False)
        if name == "control_device":
            result = await call_service(inputs["domain"], inputs["service"], inputs["data"])
            return json.dumps(result, ensure_ascii=False)
        if name == "get_memory":
            notes = await get_notes(user_id)
            return json.dumps(notes, ensure_ascii=False) if notes else "Nessuna nota salvata."
        if name == "save_memory":
            await save_note(user_id, inputs["key"], inputs["value"])
            return f"Nota '{inputs['key']}' salvata."
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
        "Sei integrata in Home Assistant. Rispondi in italiano, in modo conciso e utile. "
        "Puoi controllare dispositivi, leggere stati della casa e salvare note tramite i tool disponibili."
    )
    if context:
        base += f"\n\nContesto utente: {context}"
    return base


async def chat(user_id: str, user_text: str, user_config: dict) -> str:
    history = await get_history(user_id)
    await save_turn(user_id, "user", user_text)

    messages = history + [{"role": "user", "content": user_text}]
    system = _build_system(user_config)

    while True:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = await _run_tool(block.name, block.input, user_id)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user", "content": tool_results})
            continue

        text_blocks = [b.text for b in response.content if hasattr(b, "text")]
        reply = "\n".join(text_blocks)
        await save_turn(user_id, "assistant", reply)
        return reply
