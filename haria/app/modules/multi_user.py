"""Modulo multi_user: invio messaggi proattivi ad altri membri via Telegram."""
import json
import config as cfg
import notifier

NAME = "multi_user"

TOOLS = [
    {
        "name": "send_message_to_user",
        "description": (
            "Invia un messaggio Telegram proattivo a un altro membro della famiglia "
            "(es. per chiedere dati a Marina). Usa il nome del membro destinatario."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string", "description": "Nome del membro destinatario (es. Marina)"},
                "message": {"type": "string", "description": "Testo del messaggio da inviare"},
            },
            "required": ["member", "message"],
        },
    },
]

PROMPT = (
    "\n- Per chiedere informazioni a un altro membro o avvisarlo, usa send_message_to_user col suo nome:"
    " HARIA gli scrive direttamente su Telegram. NON dire che non puoi contattare altri utenti."
)


def _find_user(member: str) -> dict | None:
    target = (member or "").strip().lower()
    for u in cfg.get("users", []):
        if str(u.get("name", "")).strip().lower() == target:
            return u
    return None


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name == "send_message_to_user":
        member = inputs["member"]
        user = _find_user(member)
        if not user:
            return f"Membro '{member}' non trovato tra gli utenti configurati."
        chat_id = user.get("chat_id")
        if not chat_id:
            return f"'{member}' non ha un chat_id Telegram: non posso scrivergli."
        try:
            await notifier.send(chat_id, inputs["message"])
            return json.dumps({"ok": True, "to": member}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    return f"Tool sconosciuto nel modulo {NAME}: {name}"
