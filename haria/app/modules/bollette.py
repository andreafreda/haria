"""Modulo bollette: storage + dashboard Consumi (corrente/acqua/gas/…).

Fonte di verita' UNICA = tabella `bollette` nel DB HARIA (persistente). L'utente
manda il PDF al bot; Claude estrae utility/periodo/consumo/costo e chiama
update_bill, che scrive nel DB e ripubblica i sensori MQTT (mqtt_pub) letti
dalla dashboard. Niente piu' input_text/script/snapshot/automazioni HA.

Le utenze sono definite in bollette_def.UTILITIES: aggiungerne una (es. telefono)
non richiede modifiche qui.
"""
import json

import bollette_def
from bollette_def import UTILITIES, norm_utility, consumo_metric, consumo_unit
from memory import (
    set_bolletta_range, get_bolletta_existing_range,
)
import mqtt_pub

NAME = "bollette"

_MESI = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
         "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]


def _mese_idx(m) -> int | None:
    """Mese (int 1-12 o nome italiano) -> indice 0-based, o None."""
    try:
        i = int(m)
        if 1 <= i <= 12:
            return i - 1
    except (ValueError, TypeError):
        pass
    if isinstance(m, str):
        t = m.strip().capitalize()
        if t in _MESI:
            return _MESI.index(t)
    return None


# enum utility dinamico dal registry (estendibile senza toccare lo schema)
_UTIL_ENUM = list(UTILITIES.keys())
_UTIL_DESC = ", ".join(f"{k} ({d['consumo_unit']})" for k, d in UTILITIES.items())

TOOLS = [
    {
        "name": "update_bill",
        "description": (
            "Aggiorna la dashboard Consumi con i dati di una bolletta. "
            "Usa quando l'utente manda il PDF di una bolletta: estrai utility, anno, mese di inizio "
            "e fine del periodo fatturato, consumo (unita' per utility) e costo totale €. "
            "Se il periodo è un solo mese, ometti month_end. Passa almeno consumo o costo. "
            f"Utility supportate: {_UTIL_DESC}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "utility": {"type": "string", "enum": _UTIL_ENUM, "description": "Tipo bolletta"},
                "year": {"type": "integer", "description": "Anno del periodo fatturato (es. 2026)"},
                "month_start": {"type": "integer", "description": "Mese inizio periodo 1-12"},
                "month_end": {"type": "integer", "description": "Mese fine periodo 1-12; ometti se mese singolo"},
                "consumo": {"type": "number", "description": "Consumo totale periodo (unita' della utility)"},
                "costo": {"type": "number", "description": "Costo totale periodo in €"},
                "confirm": {"type": "boolean", "description": "Metti true SOLO se l'utente conferma di voler sovrascrivere dati già registrati per quel periodo. Default false."},
            },
            "required": ["utility", "year", "month_start"],
        },
    },
]

PROMPT = ""
try:
    import prompts
    PROMPT = prompts.get("module_bollette")
except Exception:
    PROMPT = ""


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name != "update_bill":
        return f"Tool sconosciuto nel modulo {NAME}: {name}"

    u = norm_utility(inputs.get("utility"))
    if not u:
        return f"Utility non valida. Supportate: {', '.join(_UTIL_ENUM)}."
    try:
        year = int(inputs["year"])
    except (ValueError, TypeError, KeyError):
        return "Anno mancante o non valido."

    i_s = _mese_idx(inputs.get("month_start"))
    if i_s is None:
        return "Mese inizio mancante o non valido (1-12)."
    i_e = _mese_idx(inputs.get("month_end")) if inputs.get("month_end") not in (None, "") else i_s
    if i_e is None:
        i_e = i_s
    if i_e < i_s:
        i_e = i_s

    consumo = inputs.get("consumo")
    costo = inputs.get("costo")
    if consumo is None and costo is None:
        return "Serve almeno consumo o costo."

    c_metric = consumo_metric(u)
    unit = consumo_unit(u)
    confirm = bool(inputs.get("confirm", False))

    # dedup: se non confermato, controlla slot gia' valorizzati nel range
    if not confirm:
        existing = {}
        if consumo is not None:
            h = await get_bolletta_existing_range(u, c_metric, year, i_s + 1, i_e + 1)
            if h:
                existing["consumo"] = [{"mese": _MESI[m - 1], "valore": v, "unita": unit} for m, v in h]
        if costo is not None:
            h = await get_bolletta_existing_range(u, "costo", year, i_s + 1, i_e + 1)
            if h:
                existing["costo"] = [{"mese": _MESI[m - 1], "valore": v} for m, v in h]
        if existing:
            periodo = _periodo(i_s, i_e, year)
            return json.dumps({
                "ok": False,
                "gia_registrato": True,
                "utility": u,
                "periodo": periodo,
                "esistente": existing,
                "msg": ("Periodo già registrato. Mostra all'utente i valori esistenti vs i nuovi e "
                        "chiedi conferma; poi richiama update_bill con confirm=true per sovrascrivere."),
            }, ensure_ascii=False)

    done = []
    if consumo is not None:
        await set_bolletta_range(u, c_metric, year, i_s + 1, i_e + 1, float(consumo))
        done.append(f"{consumo} {unit}")
    if costo is not None:
        await set_bolletta_range(u, "costo", year, i_s + 1, i_e + 1, float(costo))
        done.append(f"€{costo}")

    # ripubblica i sensori MQTT (non bloccante) -> dashboard aggiornata
    mqtt_pub.request_bollette_refresh()

    periodo = _periodo(i_s, i_e, year)
    return json.dumps({"ok": True, "utility": u, "periodo": periodo, "salvato": done},
                      ensure_ascii=False)


def _periodo(i_s: int, i_e: int, year: int) -> str:
    if i_e == i_s:
        return f"{_MESI[i_s]} {year}"
    return f"{_MESI[i_s]} - {_MESI[i_e]} {year}"


async def seed_from_ha() -> int:
    """Migrazione una-tantum: se la tabella bollette e' vuota, importa i valori
    dagli input_text.csv_* esistenti su HA. Ritorna il numero di mesi importati."""
    from datetime import date
    from memory import bollette_is_empty, seed_bollette
    if not await bollette_is_empty():
        return 0
    from ha_client import get_states
    years = range(2022, date.today().year + 2)
    rows = []
    for util, d in UTILITIES.items():
        for metric in (d["consumo_metric"], "costo"):
            for y in years:
                eid = f"input_text.csv_{util}_{metric}_{y}"
                try:
                    st = await get_states([eid])
                except Exception:
                    continue
                if not st:
                    continue
                state = (st[0].get("state") or "").strip()
                if not state or state in ("unknown", "unavailable"):
                    continue
                for i, p in enumerate(state.split(",")[:12]):
                    try:
                        v = float(p.strip())
                    except (ValueError, AttributeError):
                        v = 0.0
                    if v != 0.0:
                        rows.append((util, metric, y, i + 1, v))
    await seed_bollette(rows)
    return len(rows)
