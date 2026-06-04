"""Modulo bollette: aggiorna la dashboard Consumi (corrente/acqua/gas) dai dati
di una bolletta. L'utente manda il PDF al bot; Claude legge il PDF (già passato
come documento) ed estrae utility, periodo, consumo e costo, poi chiama
update_bill che riusa gli script HA esistenti (script.salva_consumi_*,
script.salva_costo_*) per scrivere i CSV mensili.
"""
import json
import asyncio
from ha_client import call_service

NAME = "bollette"

_MESI = ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno",
         "Luglio", "Agosto", "Settembre", "Ottobre", "Novembre", "Dicembre"]

# entità input_number che contiene il valore di consumo, per utility
_CONS_VAL = {
    "corrente": "input_number.bolletta_corrente_kwh",
    "acqua": "input_number.bolletta_acqua_m3",
    "gas": "input_number.bolletta_gas_m3",
}
_UNIT = {"corrente": "kWh", "acqua": "m³", "gas": "m³"}


def _norm_utility(u: str) -> str | None:
    t = (u or "").strip().lower()
    if t in ("corrente", "luce", "elettricità", "elettricita", "energia", "elettrica"):
        return "corrente"
    if t in ("acqua", "idrica"):
        return "acqua"
    if t in ("gas", "metano"):
        return "gas"
    return None


def _mese_name(m) -> str | None:
    try:
        i = int(m)
        if 1 <= i <= 12:
            return _MESI[i - 1]
    except (ValueError, TypeError):
        pass
    if isinstance(m, str):
        t = m.strip().capitalize()
        if t in _MESI:
            return t
    return None


TOOLS = [
    {
        "name": "update_bill",
        "description": (
            "Aggiorna la dashboard Consumi con i dati di una bolletta (corrente/acqua/gas). "
            "Usa quando l'utente manda il PDF di una bolletta: estrai utility, anno, mese di inizio "
            "e fine del periodo fatturato, consumo (kWh per corrente, m³ per acqua/gas) e costo totale €. "
            "Se il periodo è un solo mese, ometti month_end. Passa almeno consumo o costo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "utility": {"type": "string", "enum": ["corrente", "acqua", "gas"],
                            "description": "Tipo bolletta"},
                "year": {"type": "integer", "description": "Anno del periodo fatturato (es. 2026)"},
                "month_start": {"type": "integer", "description": "Mese inizio periodo 1-12"},
                "month_end": {"type": "integer", "description": "Mese fine periodo 1-12; ometti se mese singolo"},
                "consumo": {"type": "number", "description": "Consumo totale periodo (kWh o m³)"},
                "costo": {"type": "number", "description": "Costo totale periodo in €"},
            },
            "required": ["utility", "year", "month_start"],
        },
    },
]

PROMPT = (
    "\n- BOLLETTE: se l'utente manda il PDF di una bolletta (corrente/luce, acqua, gas), leggi il documento, "
    "estrai utility, anno, mese inizio/fine del periodo fatturato, consumo (kWh corrente, m³ acqua/gas) e costo "
    "totale €, poi chiama update_bill. Se un dato non è chiaro nel PDF, chiedi conferma prima di salvare. "
    "Mese singolo: ometti month_end. Aggiorna sia consumo sia costo se presenti."
)


async def _set(entity_id: str, service: str, value) -> None:
    domain = entity_id.split(".", 1)[0]
    await call_service(domain, service, {"entity_id": entity_id, "value": value})


async def _save_consumi(u: str, year: int, m_s: str, m_e: str, consumo: float) -> None:
    await call_service("input_select", "select_option",
                       {"entity_id": f"input_select.bolletta_{u}_consumi_mese_inizio", "option": m_s})
    await call_service("input_select", "select_option",
                       {"entity_id": f"input_select.bolletta_{u}_consumi_mese_fine", "option": m_e})
    await _set(f"input_number.bolletta_{u}_consumi_anno", "set_value", year)
    await _set(_CONS_VAL[u], "set_value", consumo)
    await call_service("script", f"salva_consumi_{u}", {})


async def _save_costo(u: str, year: int, m_s: str, m_e: str, costo: float) -> None:
    await call_service("input_select", "select_option",
                       {"entity_id": f"input_select.bolletta_{u}_mese_inizio", "option": m_s})
    await call_service("input_select", "select_option",
                       {"entity_id": f"input_select.bolletta_{u}_mese_fine", "option": m_e})
    await _set(f"input_number.bolletta_{u}_anno", "set_value", year)
    await _set(f"input_number.bolletta_{u}_costo", "set_value", costo)
    await call_service("script", f"salva_costo_{u}", {})


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name != "update_bill":
        return f"Tool sconosciuto nel modulo {NAME}: {name}"

    u = _norm_utility(inputs.get("utility"))
    if not u:
        return "Utility non valida. Usa corrente, acqua o gas."
    try:
        year = int(inputs["year"])
    except (ValueError, TypeError, KeyError):
        return "Anno mancante o non valido."
    m_s = _mese_name(inputs.get("month_start"))
    if not m_s:
        return "Mese inizio mancante o non valido (1-12)."
    m_e = _mese_name(inputs.get("month_end")) if inputs.get("month_end") not in (None, "") else "—"

    consumo = inputs.get("consumo")
    costo = inputs.get("costo")
    if consumo is None and costo is None:
        return "Serve almeno consumo o costo."

    done = []
    if consumo is not None:
        await _save_consumi(u, year, m_s, m_e, float(consumo))
        done.append(f"{consumo} {_UNIT[u]}")
    if costo is not None:
        await _save_costo(u, year, m_s, m_e, float(costo))
        done.append(f"€{costo}")

    periodo = m_s + (f" - {m_e}" if m_e != "—" else "") + f" {year}"
    return json.dumps({"ok": True, "utility": u, "periodo": periodo, "salvato": done},
                      ensure_ascii=False)
