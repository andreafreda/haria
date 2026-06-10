"""Modulo economia domestica: registrazione movimenti (spese/entrate) via chat.

Fonte di verita' = tabelle econ_conti/econ_transazioni nel DB HARIA (memory.py).
I conti sono definiti in econ_def.CONTI (bancoposta, postepay, paypal, contanti);
se l'utente non specifica il conto si assume 'contanti' (caso d'uso tipico:
"ho speso 20 euro per la frutta").
"""
import json
import sqlite3
from datetime import date

import econ_def
from econ_def import norm_conto
import memory
from memory import (
    add_transazione, get_saldo, get_saldi, riepilogo_spese,
    normalize_categoria, list_categorie, rename_categoria, merge_categoria,
    reset_economia,
)

NAME = "economia"

_CONTI_DESC = ", ".join(econ_def.CONTI.keys())

TOOLS = [
    {
        "name": "add_transazione",
        "description": (
            "Registra una spesa o un'entrata di denaro. Usa quando l'utente racconta "
            "una spesa fatta o un incasso ricevuto (es. 'ho speso 20 euro per la frutta', "
            "'ho pagato 35 euro di benzina con la postepay', 'ho ricevuto 50 euro di rimborso'). "
            f"Conti disponibili: {_CONTI_DESC}. Se l'utente non specifica il conto, "
            "viene usato 'contanti'. Categoria: termine breve e generico (es. 'alimentari', "
            "'trasporti', 'bollette', 'svago', 'salute', 'shopping', 'stipendio')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tipo": {"type": "string", "enum": ["spesa", "entrata"], "description": "spesa = soldi usciti, entrata = soldi entrati"},
                "importo": {"type": "number", "description": "Importo in euro, sempre positivo"},
                "categoria": {"type": "string", "description": "Categoria della spesa/entrata"},
                "descrizione": {"type": "string", "description": "Breve descrizione (es. 'frutta', 'benzina')"},
                "conto": {"type": "string", "description": f"Conto su cui registrare il movimento ({_CONTI_DESC}). Default: contanti"},
                "data": {"type": "string", "description": "Data movimento YYYY-MM-DD. Default: oggi"},
            },
            "required": ["tipo", "importo", "categoria"],
        },
    },
    {
        "name": "get_saldo",
        "description": (
            "Mostra il saldo dei conti. Usa quando l'utente chiede quanto ha sul conto/carta "
            "(es. 'quanto ho sulla postepay?', 'quanti soldi ho in totale?'). "
            "Senza 'conto' ritorna il saldo di tutti i conti."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "conto": {"type": "string", "description": f"Conto specifico ({_CONTI_DESC}); ometti per tutti"},
            },
        },
    },
    {
        "name": "riepilogo_spese",
        "description": (
            "Riepilogo di entrate, uscite e spese per categoria in un periodo. Usa quando "
            "l'utente chiede un resoconto (es. 'quanto ho speso questo mese?', "
            "'riepilogo spese di maggio', 'dove sono finiti i soldi?'). "
            "Passa data_da e data_a in formato YYYY-MM-DD per delimitare il periodo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "data_da": {"type": "string", "description": "Inizio periodo YYYY-MM-DD"},
                "data_a": {"type": "string", "description": "Fine periodo YYYY-MM-DD"},
                "conto": {"type": "string", "description": f"Limita a un conto ({_CONTI_DESC}); ometti per tutti"},
            },
        },
    },
    {
        "name": "gestisci_categorie",
        "description": (
            "Gestisce le categorie di spesa/entrata. Usa quando l'utente vuole vedere "
            "le categorie ('che categorie ci sono?'), rinominarne una "
            "('chiama 'spesa' come 'alimentari'') o unirne due perché doppioni "
            "('unisci cibo e alimentari'). Per evitare doppioni, prima di creare una "
            "categoria nuova controlla se esiste già una equivalente con 'lista'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "azione": {"type": "string", "enum": ["lista", "rinomina", "unisci"], "description": "lista = mostra tutte; rinomina = cambia nome; unisci = fonde due categorie"},
                "da": {"type": "string", "description": "Per rinomina/unisci: categoria sorgente"},
                "a": {"type": "string", "description": "Per rinomina/unisci: nuovo nome / categoria destinazione"},
            },
            "required": ["azione"],
        },
    },
    {
        "name": "reset_economia",
        "description": (
            "Azzera i dati del modulo economia. Operazione DISTRUTTIVA e "
            "irreversibile: cancella tutte le transazioni registrate. Usa solo se "
            "l'utente chiede esplicitamente di ripulire/azzerare i dati (es. fine "
            "fase di test). Senza confirm=true non cancella nulla, mostra solo cosa "
            "verrebbe rimosso e chiede conferma."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "confirm": {"type": "boolean", "description": "Metti true SOLO dopo conferma esplicita dell'utente. Default false = anteprima."},
                "reset_categorie": {"type": "boolean", "description": "Se true ripristina le categorie al set di default. Default false."},
                "reset_saldi": {"type": "boolean", "description": "Se true azzera i saldi iniziali dei conti. Default false."},
            },
        },
    },
]

PROMPT = ""
try:
    import prompts
    PROMPT = prompts.get("module_economia")
except Exception:
    PROMPT = ""


async def handle(name: str, inputs: dict, user_id: str) -> str:
    if name == "add_transazione":
        return await _add_transazione(inputs)
    if name == "get_saldo":
        return await _get_saldo(inputs)
    if name == "riepilogo_spese":
        return await _riepilogo_spese(inputs)
    if name == "gestisci_categorie":
        return await _gestisci_categorie(inputs)
    if name == "reset_economia":
        return await _reset_economia(inputs)
    return f"Tool sconosciuto nel modulo {NAME}: {name}"


async def _reset_economia(inputs: dict) -> str:
    reset_categorie = bool(inputs.get("reset_categorie", False))
    reset_saldi = bool(inputs.get("reset_saldi", False))
    if not inputs.get("confirm", False):
        # anteprima: mostra i saldi attuali senza cancellare nulla
        saldi = await get_saldi()
        return json.dumps({
            "ok": False,
            "conferma_richiesta": True,
            "msg": ("Operazione DISTRUTTIVA: cancella tutte le transazioni"
                    + (", ripristina le categorie di default" if reset_categorie else "")
                    + (", azzera i saldi iniziali" if reset_saldi else "")
                    + ". Mostra all'utente cosa verrà rimosso e chiedi conferma "
                      "esplicita; poi richiama reset_economia con confirm=true."),
            "saldi_attuali": saldi,
        }, ensure_ascii=False)
    res = await reset_economia(reset_categorie=reset_categorie, reset_saldi=reset_saldi)
    res["ok"] = True
    return json.dumps(res, ensure_ascii=False)


async def _gestisci_categorie(inputs: dict) -> str:
    azione = (inputs.get("azione") or "").strip().lower()
    if azione == "lista":
        cats = await list_categorie()
        return json.dumps({"ok": True, "categorie": cats}, ensure_ascii=False)
    da = (inputs.get("da") or "").strip()
    a = (inputs.get("a") or "").strip()
    if azione == "rinomina":
        if not da or not a:
            return "Per rinominare servono 'da' (categoria esistente) e 'a' (nuovo nome)."
        ok = await rename_categoria(da, a)
        if not ok:
            return f"Categoria '{da}' non trovata."
        return json.dumps({"ok": True, "azione": "rinomina", "da": da, "a": a.lower()}, ensure_ascii=False)
    if azione == "unisci":
        if not da or not a:
            return "Per unire servono 'da' (categoria da assorbire) e 'a' (categoria destinazione)."
        ok = await merge_categoria(da, a)
        if not ok:
            return f"Impossibile unire: '{da}' non trovata o destinazione non valida."
        return json.dumps({"ok": True, "azione": "unisci", "da": da, "a": a.lower()}, ensure_ascii=False)
    return "Azione non valida: usa 'lista', 'rinomina' o 'unisci'."


async def _get_saldo(inputs: dict) -> str:
    conto_raw = (inputs.get("conto") or "").strip()
    if conto_raw:
        conto = norm_conto(conto_raw)
        if conto is None:
            return f"Conto '{conto_raw}' non riconosciuto. Conti disponibili: {_CONTI_DESC}."
        saldo = await get_saldo(conto)
        return json.dumps({"ok": True, "conto": conto, "saldo": saldo}, ensure_ascii=False)
    saldi = await get_saldi()
    totale = round(sum(s["saldo"] for s in saldi), 2)
    return json.dumps({"ok": True, "saldi": saldi, "totale": totale}, ensure_ascii=False)


async def _riepilogo_spese(inputs: dict) -> str:
    conto_raw = (inputs.get("conto") or "").strip()
    conto = None
    if conto_raw:
        conto = norm_conto(conto_raw)
        if conto is None:
            return f"Conto '{conto_raw}' non riconosciuto. Conti disponibili: {_CONTI_DESC}."
    data_da = (inputs.get("data_da") or "").strip() or None
    data_a = (inputs.get("data_a") or "").strip() or None
    rep = await riepilogo_spese(data_da=data_da, data_a=data_a, conto=conto)
    rep["ok"] = True
    rep["conto"] = conto or "tutti"
    rep["periodo"] = {"da": data_da or "inizio", "a": data_a or "oggi"}
    return json.dumps(rep, ensure_ascii=False)


async def _add_transazione(inputs: dict) -> str:
    tipo = inputs.get("tipo")
    if tipo not in ("spesa", "entrata"):
        return "Tipo non valido: deve essere 'spesa' o 'entrata'."

    try:
        importo = abs(float(inputs["importo"]))
    except (KeyError, ValueError, TypeError):
        return "Importo mancante o non valido."
    if importo == 0:
        return "Importo non può essere zero."

    categoria = (inputs.get("categoria") or "").strip()
    if not categoria:
        return "Categoria mancante."

    descrizione = (inputs.get("descrizione") or "").strip()

    conto_raw = (inputs.get("conto") or "").strip()
    conto = norm_conto(conto_raw) if conto_raw else "contanti"
    if conto is None:
        return f"Conto '{conto_raw}' non riconosciuto. Conti disponibili: {_CONTI_DESC}."

    data = (inputs.get("data") or "").strip() or date.today().isoformat()

    # normalizza categoria: riusa una canonica esistente (case-insensitive),
    # altrimenti la registra. Evita doppioni tipo "Cibo"/"cibo"/"alimentari".
    categoria = await normalize_categoria(categoria)

    importo_firmato = importo if tipo == "entrata" else -importo
    await add_transazione(conto, data, importo_firmato, categoria, descrizione)
    saldo = await get_saldo(conto)

    return json.dumps({
        "ok": True,
        "conto": conto,
        "tipo": tipo,
        "importo": importo,
        "categoria": categoria,
        "descrizione": descrizione,
        "data": data,
        "saldo_aggiornato": saldo,
    }, ensure_ascii=False)


def _categorie_sync() -> list[str]:
    """Lettura sync delle categorie per il prompt (dynamic_prompt e' sync).
    Usa sqlite3 in sola lettura su memory.DB_PATH; best-effort."""
    try:
        con = sqlite3.connect(memory.DB_PATH, timeout=2.0)
        try:
            rows = con.execute("SELECT nome FROM econ_categorie ORDER BY nome").fetchall()
        finally:
            con.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def dynamic_prompt() -> str:
    """Inietta le categorie esistenti così Claude riusa quelle invece di
    inventarne di nuove (normalizzazione proattiva anti-doppioni)."""
    cats = _categorie_sync()
    if not cats:
        return ""
    return ("\n- ECONOMIA categorie esistenti (riusa queste quando registri spese/entrate, "
            "non crearne di simili: se serve una nuova davvero diversa va bene): "
            + ", ".join(cats) + ".")
