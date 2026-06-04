"""Modulo todo: integrazione con le liste todo native di Home Assistant.

Usa i servizi todo.* di HA (todo.add_item, todo.update_item, todo.remove_item,
todo.get_items). Le liste sono entità todo.* già esistenti in HA (es. la lista
della spesa), visibili ovunque: dashboard HA, app companion, widget.
"""
import json
from ha_client import get_states, call_service

NAME = "todo"


async def _todo_entities() -> list[dict]:
    states = await get_states(None)
    return [
        {"entity_id": s["entity_id"],
         "name": s["attributes"].get("friendly_name", s["entity_id"])}
        for s in states if s["entity_id"].startswith("todo.")
    ]


async def _resolve_list(name: str | None) -> str | None:
    """Risolve un nome lista (testo libero) in entity_id todo.*."""
    ents = await _todo_entities()
    if not ents:
        return None
    if not name:
        return ents[0]["entity_id"]  # prima lista come default
    target = name.strip().lower()
    # match esatto entity_id
    for e in ents:
        if e["entity_id"].lower() == target:
            return e["entity_id"]
    # match parziale su nome friendly o object_id
    for e in ents:
        if target in e["name"].lower() or target in e["entity_id"].lower():
            return e["entity_id"]
    return None


TOOLS = [
    {
        "name": "list_todo_lists",
        "description": "Elenca le liste todo/spesa disponibili in Home Assistant (entità todo.*).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_todo_items",
        "description": "Leggi gli elementi di una lista todo/spesa HA. Se non indichi la lista usa la prima disponibile.",
        "input_schema": {
            "type": "object",
            "properties": {
                "list": {"type": "string", "description": "Nome o entity_id della lista (es. 'spesa', 'todo.lista_della_spesa')"},
                "status": {"type": "string", "enum": ["needs_action", "completed"], "description": "Filtra per stato (default tutti)"},
            },
        },
    },
    {
        "name": "add_todo_item",
        "description": "Aggiungi un elemento a una lista todo/spesa HA (es. 'aggiungi latte alla spesa').",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Cosa aggiungere"},
                "list": {"type": "string", "description": "Nome o entity_id lista; default prima lista"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "complete_todo_item",
        "description": "Segna un elemento come completato/preso in una lista todo/spesa HA.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Nome esatto dell'elemento"},
                "list": {"type": "string", "description": "Nome o entity_id lista; default prima lista"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "remove_todo_item",
        "description": "Rimuovi un elemento da una lista todo/spesa HA.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Nome esatto dell'elemento"},
                "list": {"type": "string", "description": "Nome o entity_id lista; default prima lista"},
            },
            "required": ["item"],
        },
    },
]

PROMPT = (
    "\n- LISTE HA: per liste della spesa/todo native di Home Assistant usa i tool todo "
    "(add_todo_item, get_todo_items, complete_todo_item, remove_todo_item, list_todo_lists). "
    "Queste liste sono visibili nell'app e nella dashboard HA. Se l'utente non specifica la lista, usa la prima disponibile."
)


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name == "list_todo_lists":
        ents = await _todo_entities()
        return json.dumps(ents, ensure_ascii=False) if ents else "Nessuna lista todo trovata in HA."

    list_id = await _resolve_list(inputs.get("list"))
    if name != "list_todo_lists" and not list_id:
        return f"Lista '{inputs.get('list')}' non trovata in HA. Usa list_todo_lists per vedere quelle disponibili."

    if name == "get_todo_items":
        data = {"entity_id": list_id}
        if inputs.get("status"):
            data["status"] = inputs["status"]
        resp = await call_service("todo", "get_items", data, return_response=True)
        sr = resp.get("service_response", {}) if isinstance(resp, dict) else {}
        items = sr.get(list_id, {}).get("items", [])
        return json.dumps({"list": list_id, "items": items}, ensure_ascii=False)

    if name == "add_todo_item":
        await call_service("todo", "add_item", {"entity_id": list_id, "item": inputs["item"]})
        return json.dumps({"ok": True, "added": inputs["item"], "list": list_id}, ensure_ascii=False)

    if name == "complete_todo_item":
        await call_service("todo", "update_item",
                           {"entity_id": list_id, "item": inputs["item"], "status": "completed"})
        return json.dumps({"ok": True, "completed": inputs["item"], "list": list_id}, ensure_ascii=False)

    if name == "remove_todo_item":
        await call_service("todo", "remove_item", {"entity_id": list_id, "item": inputs["item"]})
        return json.dumps({"ok": True, "removed": inputs["item"], "list": list_id}, ensure_ascii=False)

    return f"Tool sconosciuto nel modulo {NAME}: {name}"
