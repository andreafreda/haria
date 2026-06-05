"""Modulo agenda: unifica promemoria (ping temporizzati), task (todo HA) ed
eventi (calendar HA) in un'unica funzionalità.

- Promemoria one-shot/ricorrenti -> scheduler APScheduler + storage SQLite HARIA.
- Task -> liste todo native HA (todo.*), con owner multipli e scadenza.
- Eventi -> calendari locali HA (calendar.haria_<membro>), owner multipli =
  evento replicato sul calendario di ogni owner; nessun owner = su tutti.
- agenda_overview legge tutto insieme (promemoria + task + eventi prossimi) per
  evitare la frammentazione tra i tre mondi.
"""
import json
from datetime import datetime, timedelta

import scheduler
from memory import (add_reminder, get_user_reminders, deactivate_reminder,
                    update_reminder as _update_reminder)
from ha_client import get_states, call_service, ws_command, get_calendar_events
import prompts

NAME = "agenda"


def _norm_dt(v):
    """Normalizza start/end evento (dict {dateTime|date} o stringa ISO) -> stringa."""
    if isinstance(v, dict):
        return v.get("dateTime") or v.get("date")
    return v


async def _list_events(cal: str, start: datetime, end: datetime) -> list[dict]:
    """Eventi di UN calendario via REST /api/calendars (include uid, necessario
    per delete/update; il servizio calendar.get_events NON ritorna l'uid)."""
    return await get_calendar_events(
        cal, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))


async def _find_events(cals: list[str], title: str | None,
                       start: datetime, end: datetime) -> list[dict]:
    """Trova eventi che matchano `title` (substring, case-insensitive) nei calendari."""
    tl = (title or "").strip().lower()
    out = []
    for c in cals:
        for e in await _list_events(c, start, end):
            s = (e.get("summary") or "").lower()
            if not tl or tl in s:
                out.append({"calendar": c, "uid": e.get("uid"),
                            "summary": e.get("summary"),
                            "start": e.get("start"), "end": e.get("end"),
                            "recurrence_id": e.get("recurrence_id")})
    return out


# ---------- helpers calendar ----------

async def _calendars() -> list[dict]:
    states = await get_states(None)
    return [
        {"entity_id": s["entity_id"],
         "name": s["attributes"].get("friendly_name", s["entity_id"])}
        for s in states if s["entity_id"].startswith("calendar.")
    ]


async def _resolve_calendars(owners: list[str] | None) -> list[str]:
    """Mappa lista owner -> calendari. Nessun owner = tutti i calendari."""
    cals = await _calendars()
    if not cals:
        return []
    if not owners:
        return [c["entity_id"] for c in cals]
    out = []
    for o in owners:
        t = o.strip().lower()
        if not t:
            continue
        m = next((c["entity_id"] for c in cals
                  if t in c["entity_id"].lower() or t in c["name"].lower()), None)
        if m and m not in out:
            out.append(m)
    return out or [c["entity_id"] for c in cals]


# ---------- helpers todo ----------

async def _todo_entities() -> list[dict]:
    states = await get_states(None)
    return [
        {"entity_id": s["entity_id"],
         "name": s["attributes"].get("friendly_name", s["entity_id"])}
        for s in states if s["entity_id"].startswith("todo.")
    ]


async def _resolve_list(name: str | None) -> str | None:
    ents = await _todo_entities()
    if not ents:
        return None
    if not name:
        return ents[0]["entity_id"]
    target = name.strip().lower()
    for e in ents:
        if e["entity_id"].lower() == target:
            return e["entity_id"]
    for e in ents:
        if target in e["name"].lower() or target in e["entity_id"].lower():
            return e["entity_id"]
    return None


def _owners_list(inputs: dict) -> list[str]:
    """Accetta owners (lista o stringa CSV) oppure owner singolo."""
    raw = inputs.get("owners") or inputs.get("owner")
    if not raw:
        return []
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return [str(x).strip() for x in raw if str(x).strip()]


TOOLS = [
    # --- promemoria (ping temporizzati) ---
    {
        "name": "set_reminder",
        "description": (
            "Crea un promemoria che ti AVVISA (notifica) a un orario. Usa per 'tra mezzora dimmi X', "
            "'ricordami alle 18'. remind_at (ISO datetime) per one-shot, OPPURE recurring (cron 5 campi) "
            "per ricorrenti. Per appuntamenti su calendario usa add_event; per cose da fare senza avviso usa add_task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Testo del promemoria"},
                "remind_at": {"type": "string", "description": "ISO datetime one-shot"},
                "recurring": {"type": "string", "description": "Cron 5 campi per ricorrenti"},
            },
            "required": ["message"],
        },
    },
    {
        "name": "list_reminders",
        "description": "Elenca i promemoria attivi (ping) dell'utente corrente.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_reminder",
        "description": "Cancella un promemoria tramite id (da list_reminders).",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "ID promemoria"}},
            "required": ["id"],
        },
    },
    {
        "name": "update_reminder",
        "description": (
            "Modifica un promemoria esistente (da list_reminders). Passa id e i campi da cambiare: "
            "message (testo), remind_at (ISO datetime one-shot) o recurring (cron 5 campi). "
            "Cambiare orario/cron riprogramma l'avviso."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "ID promemoria"},
                "message": {"type": "string", "description": "Nuovo testo"},
                "remind_at": {"type": "string", "description": "Nuovo ISO datetime one-shot"},
                "recurring": {"type": "string", "description": "Nuovo cron 5 campi (vuoto = rimuovi ricorrenza)"},
            },
            "required": ["id"],
        },
    },
    # --- task (todo HA) ---
    {
        "name": "list_todo_lists",
        "description": "Elenca le liste todo/spesa disponibili in Home Assistant (entità todo.*).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_task",
        "description": (
            "Aggiungi un'attività (cosa da fare) a una lista todo HA. Per la spesa ('aggiungi latte alla spesa'). "
            "Per i task puoi indicare owners (uno o più responsabili) e scadenza (due_date 'YYYY-MM-DD' o due_datetime ISO). "
            "Per appuntamenti con orario usa add_event; per avvisi temporizzati usa set_reminder."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Cosa fare"},
                "list": {"type": "string", "description": "Nome/entity_id lista; default prima lista"},
                "owners": {"type": "array", "items": {"type": "string"},
                           "description": "Responsabili (es. ['Andrea','Marina']). Solo task."},
                "due_date": {"type": "string", "description": "Scadenza 'YYYY-MM-DD'"},
                "due_datetime": {"type": "string", "description": "Scadenza ISO 'YYYY-MM-DDTHH:MM:SS'"},
                "description": {"type": "string", "description": "Note"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "get_tasks",
        "description": "Leggi le attività di una lista todo HA. Default prima lista.",
        "input_schema": {
            "type": "object",
            "properties": {
                "list": {"type": "string", "description": "Nome/entity_id lista"},
                "status": {"type": "string", "enum": ["needs_action", "completed"],
                           "description": "Filtra per stato (default tutti)"},
            },
        },
    },
    {
        "name": "complete_task",
        "description": "Segna un'attività come completata in una lista todo HA.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Nome esatto dell'attività"},
                "list": {"type": "string", "description": "Nome/entity_id lista"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "remove_task",
        "description": "Rimuovi un'attività da una lista todo HA.",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Nome esatto dell'attività"},
                "list": {"type": "string", "description": "Nome/entity_id lista"},
            },
            "required": ["item"],
        },
    },
    {
        "name": "update_task",
        "description": (
            "Modifica un'attività esistente in una lista todo HA. Individua per item (nome attuale). "
            "Campi opzionali: new_item (rinomina), due_date 'YYYY-MM-DD' o due_datetime ISO, "
            "description (note), status ('needs_action' o 'completed')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Nome attuale dell'attività"},
                "list": {"type": "string", "description": "Nome/entity_id lista"},
                "new_item": {"type": "string", "description": "Nuovo nome"},
                "due_date": {"type": "string", "description": "Scadenza 'YYYY-MM-DD'"},
                "due_datetime": {"type": "string", "description": "Scadenza ISO"},
                "description": {"type": "string", "description": "Note"},
                "status": {"type": "string", "enum": ["needs_action", "completed"],
                           "description": "Stato"},
            },
            "required": ["item"],
        },
    },
    # --- eventi (calendar HA) ---
    {
        "name": "add_event",
        "description": (
            "Crea un appuntamento sul calendario HA. owners = uno o più membri: l'evento finisce sul "
            "calendario di ciascuno (nessun owner = su tutti, evento di famiglia). "
            "Usa start/end ISO per orario, oppure start_date/end_date 'YYYY-MM-DD' per tutto il giorno."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Titolo evento"},
                "owners": {"type": "array", "items": {"type": "string"},
                           "description": "Membri (es. ['Andrea']). Vuoto = famiglia (tutti)."},
                "start": {"type": "string", "description": "Inizio ISO 'YYYY-MM-DDTHH:MM:SS'"},
                "end": {"type": "string", "description": "Fine ISO 'YYYY-MM-DDTHH:MM:SS'"},
                "start_date": {"type": "string", "description": "Inizio tutto-il-giorno 'YYYY-MM-DD'"},
                "end_date": {"type": "string", "description": "Fine tutto-il-giorno 'YYYY-MM-DD' (esclusiva)"},
                "description": {"type": "string", "description": "Note"},
                "location": {"type": "string", "description": "Luogo"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "get_events",
        "description": "Elenca gli eventi calendario nei prossimi N giorni (default 7). owners filtra i calendari. Ogni evento include uid.",
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Finestra giorni (default 7)"},
                "owners": {"type": "array", "items": {"type": "string"},
                           "description": "Filtra per membro; vuoto = tutti"},
            },
        },
    },
    {
        "name": "delete_event",
        "description": (
            "Cancella appuntamenti dal calendario HA. Indica title (cerca per nome, substring) e opzionale owners "
            "per limitare i calendari. Cancella TUTTI gli eventi che matchano nella finestra (default ±60gg). "
            "Usa per rimuovere appuntamenti o ripulire duplicati."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Nome (o parte) dell'evento da cancellare"},
                "owners": {"type": "array", "items": {"type": "string"},
                           "description": "Limita ai calendari di questi membri; vuoto = tutti"},
                "days": {"type": "integer", "description": "Finestra di ricerca avanti in giorni (default 60)"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_event",
        "description": (
            "Modifica appuntamenti esistenti sul calendario HA. Individua per title; applica i nuovi campi forniti "
            "(new_title, start+end ISO oppure start_date+end_date, description, location) a tutti gli eventi che matchano."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Nome (o parte) dell'evento da modificare"},
                "owners": {"type": "array", "items": {"type": "string"},
                           "description": "Limita ai calendari di questi membri; vuoto = tutti"},
                "new_title": {"type": "string", "description": "Nuovo titolo"},
                "start": {"type": "string", "description": "Nuovo inizio ISO 'YYYY-MM-DDTHH:MM:SS'"},
                "end": {"type": "string", "description": "Nuova fine ISO"},
                "start_date": {"type": "string", "description": "Nuovo inizio tutto-il-giorno 'YYYY-MM-DD'"},
                "end_date": {"type": "string", "description": "Nuova fine tutto-il-giorno 'YYYY-MM-DD'"},
                "description": {"type": "string", "description": "Nuove note"},
                "location": {"type": "string", "description": "Nuovo luogo"},
                "days": {"type": "integer", "description": "Finestra di ricerca avanti in giorni (default 60)"},
            },
            "required": ["title"],
        },
    },
    # --- vista aggregata ---
    {
        "name": "agenda_overview",
        "description": (
            "Vista UNICA dell'agenda: promemoria attivi + attività todo aperte + eventi calendario prossimi. "
            "Usa quando l'utente chiede 'cosa ho in agenda', 'cosa devo fare', panoramica generale."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Finestra eventi/task in giorni (default 7)"},
            },
        },
    },
]

PROMPT = prompts.get("module_agenda")


# ---------- handlers ----------

async def _h_reminders(name: str, inputs: dict, user_id: str) -> str | None:
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
    if name == "update_reminder":
        rid = int(inputs["id"])
        r = await _update_reminder(
            rid, user_id,
            message=inputs.get("message"),
            remind_at=inputs.get("remind_at"),
            recurring=inputs.get("recurring"),
        )
        if not r:
            return f"Promemoria #{rid} non trovato o niente da aggiornare."
        # riprogramma se cambiato orario/cron
        if inputs.get("remind_at") is not None or inputs.get("recurring") is not None:
            scheduler.cancel_job(rid)
            if not scheduler.schedule_reminder(r):
                await deactivate_reminder(rid, user_id)
                return "Errore: nuovo orario non valido o nel passato."
        return f"Promemoria #{rid} aggiornato."
    return None


async def _h_tasks(name: str, inputs: dict, user_id: str) -> str | None:
    if name == "list_todo_lists":
        ents = await _todo_entities()
        return json.dumps(ents, ensure_ascii=False) if ents else "Nessuna lista todo in HA."

    if name in ("add_task", "get_tasks", "complete_task", "remove_task", "update_task"):
        list_id = await _resolve_list(inputs.get("list"))
        if not list_id:
            return f"Lista '{inputs.get('list')}' non trovata. Usa list_todo_lists."

        if name == "get_tasks":
            data = {"entity_id": list_id}
            if inputs.get("status"):
                data["status"] = inputs["status"]
            resp = await call_service("todo", "get_items", data, return_response=True)
            sr = resp.get("service_response", {}) if isinstance(resp, dict) else {}
            items = sr.get(list_id, {}).get("items", [])
            return json.dumps({"list": list_id, "items": items}, ensure_ascii=False)

        if name == "add_task":
            data = {"entity_id": list_id, "item": inputs["item"]}
            desc_parts = []
            owners = _owners_list(inputs)
            if owners:
                desc_parts.append("👤 " + ", ".join(owners))
            if inputs.get("description"):
                desc_parts.append(inputs["description"].strip())
            if desc_parts:
                data["description"] = " — ".join(desc_parts)
            if inputs.get("due_datetime"):
                data["due_datetime"] = inputs["due_datetime"]
            elif inputs.get("due_date"):
                data["due_date"] = inputs["due_date"]
            await call_service("todo", "add_item", data)
            return json.dumps({"ok": True, "added": inputs["item"], "list": list_id,
                               "owners": owners or None,
                               "due": data.get("due_datetime") or data.get("due_date")},
                              ensure_ascii=False)

        if name == "complete_task":
            await call_service("todo", "update_item",
                               {"entity_id": list_id, "item": inputs["item"], "status": "completed"})
            return json.dumps({"ok": True, "completed": inputs["item"], "list": list_id}, ensure_ascii=False)

        if name == "remove_task":
            await call_service("todo", "remove_item", {"entity_id": list_id, "item": inputs["item"]})
            return json.dumps({"ok": True, "removed": inputs["item"], "list": list_id}, ensure_ascii=False)

        if name == "update_task":
            data = {"entity_id": list_id, "item": inputs["item"]}
            if inputs.get("new_item"):
                data["rename"] = inputs["new_item"]
            if inputs.get("status"):
                data["status"] = inputs["status"]
            if inputs.get("description") is not None:
                data["description"] = inputs["description"]
            if inputs.get("due_datetime"):
                data["due_datetime"] = inputs["due_datetime"]
            elif inputs.get("due_date"):
                data["due_date"] = inputs["due_date"]
            if len(data) <= 2:
                return "Niente da aggiornare: fornisci new_item, status, description o scadenza."
            await call_service("todo", "update_item", data)
            return json.dumps({"ok": True, "updated": inputs["item"], "list": list_id},
                              ensure_ascii=False)
    return None


async def _h_events(name: str, inputs: dict, user_id: str) -> str | None:
    if name == "add_event":
        owners = _owners_list(inputs)
        cals = await _resolve_calendars(owners)
        if not cals:
            return "Nessun calendario HA trovato."
        ev = {"summary": inputs["title"]}
        if inputs.get("description"):
            ev["description"] = inputs["description"]
        if inputs.get("location"):
            ev["location"] = inputs["location"]
        if inputs.get("start") and inputs.get("end"):
            ev["start_date_time"] = inputs["start"]
            ev["end_date_time"] = inputs["end"]
        elif inputs.get("start_date") and inputs.get("end_date"):
            ev["start_date"] = inputs["start_date"]
            ev["end_date"] = inputs["end_date"]
        else:
            return "Errore: fornisci start+end (ISO) o start_date+end_date (giorno)."
        created = []
        for c in cals:
            await call_service("calendar", "create_event", {"entity_id": c, **ev})
            created.append(c)
        return json.dumps({"ok": True, "event": inputs["title"], "calendars": created,
                           "owners": owners or "famiglia"}, ensure_ascii=False)

    if name == "get_events":
        days = int(inputs.get("days") or 7)
        cals = await _resolve_calendars(_owners_list(inputs))
        if not cals:
            return "Nessun calendario HA trovato."
        start = datetime.now()
        end = start + timedelta(days=days)
        out = []
        for c in cals:
            for e in await _list_events(c, start, end):
                out.append({"calendar": c, "uid": e.get("uid"),
                            "summary": e.get("summary"),
                            "start": _norm_dt(e.get("start")), "end": _norm_dt(e.get("end")),
                            "location": e.get("location")})
        out.sort(key=lambda x: str(x.get("start") or ""))
        return json.dumps({"days": days, "events": out}, ensure_ascii=False)

    if name == "delete_event":
        cals = await _resolve_calendars(_owners_list(inputs))
        if not cals:
            return "Nessun calendario HA trovato."
        days = int(inputs.get("days") or 60)
        start = datetime.now() - timedelta(days=1)
        end = datetime.now() + timedelta(days=days)
        matches = await _find_events(cals, inputs.get("title"), start, end)
        if not matches:
            return json.dumps({"ok": False, "deleted": 0, "msg": "Nessun evento trovato"},
                              ensure_ascii=False)
        deleted = []
        for m in matches:
            payload = {"type": "calendar/event/delete",
                       "entity_id": m["calendar"], "uid": m["uid"]}
            if m.get("recurrence_id"):
                payload["recurrence_id"] = m["recurrence_id"]
                payload["recurrence_range"] = "THISANDFUTURE"
            await ws_command(payload)
            deleted.append({"calendar": m["calendar"], "summary": m.get("summary")})
        return json.dumps({"ok": True, "deleted": len(deleted), "events": deleted},
                          ensure_ascii=False)

    if name == "update_event":
        cals = await _resolve_calendars(_owners_list(inputs))
        if not cals:
            return "Nessun calendario HA trovato."
        days = int(inputs.get("days") or 60)
        start = datetime.now() - timedelta(days=1)
        end = datetime.now() + timedelta(days=days)
        matches = await _find_events(cals, inputs.get("title"), start, end)
        if not matches:
            return json.dumps({"ok": False, "updated": 0, "msg": "Nessun evento trovato"},
                              ensure_ascii=False)
        base_ev = {}
        if inputs.get("new_title"):
            base_ev["summary"] = inputs["new_title"]
        if inputs.get("description") is not None:
            base_ev["description"] = inputs["description"]
        if inputs.get("location") is not None:
            base_ev["location"] = inputs["location"]
        if inputs.get("start") and inputs.get("end"):
            base_ev["dtstart"] = inputs["start"]
            base_ev["dtend"] = inputs["end"]
        elif inputs.get("start_date") and inputs.get("end_date"):
            base_ev["dtstart"] = inputs["start_date"]
            base_ev["dtend"] = inputs["end_date"]
        if not base_ev:
            return "Niente da aggiornare: fornisci new_title, start+end, description o location."
        updated = []
        for m in matches:
            ev = dict(base_ev)
            # HA richiede evento completo: integra campi mancanti dall'esistente
            if "summary" not in ev:
                ev["summary"] = m.get("summary")
            if "dtstart" not in ev:
                ev["dtstart"] = _norm_dt(m.get("start"))
                ev["dtend"] = _norm_dt(m.get("end"))
            payload = {"type": "calendar/event/update", "entity_id": m["calendar"],
                       "uid": m["uid"], "event": ev}
            if m.get("recurrence_id"):
                payload["recurrence_id"] = m["recurrence_id"]
                payload["recurrence_range"] = "THISANDFUTURE"
            await ws_command(payload)
            updated.append({"calendar": m["calendar"]})
        return json.dumps({"ok": True, "updated": len(updated), "event": base_ev},
                          ensure_ascii=False)
    return None


async def _overview(inputs: dict, user_id: str) -> str:
    days = int(inputs.get("days") or 7)
    result = {}
    # promemoria
    try:
        result["reminders"] = await get_user_reminders(user_id)
    except Exception as e:
        result["reminders"] = {"error": str(e)}
    # task aperti su tutte le liste
    try:
        tasks = []
        for ent in await _todo_entities():
            resp = await call_service("todo", "get_items",
                                      {"entity_id": ent["entity_id"], "status": "needs_action"},
                                      return_response=True)
            sr = resp.get("service_response", {}) if isinstance(resp, dict) else {}
            for it in sr.get(ent["entity_id"], {}).get("items", []):
                tasks.append({"list": ent["entity_id"], "summary": it.get("summary"),
                              "due": it.get("due"), "description": it.get("description")})
        result["tasks"] = tasks
    except Exception as e:
        result["tasks"] = {"error": str(e)}
    # eventi
    try:
        result["events"] = json.loads(await _h_events("get_events", {"days": days}, user_id)).get("events", [])
    except Exception as e:
        result["events"] = {"error": str(e)}
    return json.dumps(result, ensure_ascii=False)


def _norm_uid(user_id: str) -> str:
    """Normalizza l'user_id al chat_id numerico (la chat web usa 'ha_chat_<id>').
    I promemoria vanno consegnati via Telegram con int(chat_id)."""
    if user_id and user_id.startswith("ha_chat_"):
        return user_id[len("ha_chat_"):]
    return user_id


async def handle(name: str, inputs: dict, user_id: str) -> str:
    user_id = _norm_uid(user_id)
    if name == "agenda_overview":
        return await _overview(inputs, user_id)
    for h in (_h_reminders, _h_tasks, _h_events):
        r = await h(name, inputs, user_id)
        if r is not None:
            return r
    return f"Tool sconosciuto nel modulo {NAME}: {name}"
