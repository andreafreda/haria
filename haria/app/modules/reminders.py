"""Modulo reminders: tool, prompt e handler per promemoria one-shot e ricorrenti."""
import json
import scheduler
from memory import add_reminder, get_user_reminders, deactivate_reminder

NAME = "reminders"

TOOLS = [
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

PROMPT = (
    "\n- Per promemoria usa set_reminder: calcola remind_at (ISO datetime) dalla data/ora attuale nel blocco volatile."
    " Per ricorrenti usa recurring (cron 5 campi). Usa list_reminders/cancel_reminder per gestirli."
)


async def handle(name: str, inputs: dict, user_id: str) -> str:
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
    return f"Tool sconosciuto nel modulo {NAME}: {name}"
