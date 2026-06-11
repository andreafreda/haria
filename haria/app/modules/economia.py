"""Modulo economia domestica: registrazione movimenti (spese/entrate) via chat.

Fonte di verita' = tabelle econ_conti/econ_transazioni nel DB HARIA (memory.py).
Gestione familiare profilata: i conti hanno un intestatario (econ_def.CONTI):
BancoPosta = famiglia (cointestato), PostePay/PayPal/Contanti per membro. Se
l'utente non specifica il conto si assume i contanti del membro che scrive.
"""
import json
import sqlite3
from datetime import date

import config as cfg
import econ_def
import econ_import
from econ_def import norm_conto
import memory
import mqtt_pub
from memory import (
    add_transazione, get_saldo, get_saldi, riepilogo_spese,
    normalize_categoria, list_categorie, rename_categoria, merge_categoria,
    delete_categoria, reset_economia, import_transazioni,
    set_budget, delete_budget, list_budget, get_budget_status,
    set_obiettivo, accantona, delete_obiettivo, get_obiettivi,
    list_conti, add_conto, update_conto, delete_conto,
    list_transazioni, update_transazione, delete_transazione,
)

NAME = "economia"


def _membro_from_user(user_id: str) -> str:
    """Risolve il membro (nome minuscolo) dal user_id del parlante.
    user_id = chat_id Telegram, o 'ha_chat_<chatid>' dal pannello web.
    Fallback: primo membro in econ_def.MEMBRI."""
    uid = (user_id or "").replace("ha_chat_", "").strip()
    for u in cfg.get("users", []):
        if str(u.get("chat_id", "")).strip() == uid:
            nome = str(u.get("name", "")).strip().lower()
            if nome in econ_def.MEMBRI:
                return nome
    return econ_def.MEMBRI[0] if econ_def.MEMBRI else "famiglia"


async def _resolve_conto(conto_raw: str, membro: str) -> str | None:
    """Risolve un conto: prima dal registry statico (norm_conto), poi fallback
    sul DB (conti custom creati a runtime via gestisci_conti)."""
    conto = norm_conto(conto_raw, membro)
    if conto is None:
        row = await memory.get_conto((conto_raw or "").strip().lower())
        if row is not None:
            conto = row["nome"]
    return conto


def _valida_data(s: str) -> str | None:
    """Valida una data ISO YYYY-MM-DD. Ritorna la stringa se valida, None se no."""
    s = (s or "").strip()
    if not s:
        return None
    try:
        date.fromisoformat(s)
        return s
    except ValueError:
        return None

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
                "intestatario": {"type": "string", "description": "Limita a un intestatario: andrea, marina o famiglia; ometti per tutti"},
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
                "azione": {"type": "string", "enum": ["lista", "crea", "rinomina", "unisci", "elimina"], "description": "lista; crea (nuova categoria); rinomina; unisci (fonde due); elimina (solo se non usata)"},
                "da": {"type": "string", "description": "Per rinomina/unisci: categoria sorgente"},
                "a": {"type": "string", "description": "Per rinomina/unisci: nuovo nome / categoria destinazione"},
                "nome": {"type": "string", "description": "Per crea/elimina: nome categoria"},
            },
            "required": ["azione"],
        },
    },
    {
        "name": "set_budget",
        "description": (
            "Imposta o rimuove il budget mensile di spesa per una categoria. Usa quando "
            "l'utente dice quanto vuole spendere al massimo per qualcosa "
            "(es. 'budget di 300 euro al mese per alimentari', 'metti un tetto di 100 "
            "per ristoranti'). Per rimuovere un budget passa importo 0. La categoria "
            "viene normalizzata: riusa quelle esistenti."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "categoria": {"type": "string", "description": "Categoria a cui applicare il budget"},
                "importo": {"type": "number", "description": "Tetto mensile in €. 0 = rimuove il budget."},
            },
            "required": ["categoria", "importo"],
        },
    },
    {
        "name": "get_budget_status",
        "description": (
            "Mostra lo stato dei budget per il mese: quanto speso vs budget, residuo e "
            "percentuale per ogni categoria con un tetto impostato. Usa quando l'utente "
            "chiede come va col budget ('sono nei budget?', 'quanto mi resta per "
            "alimentari?', 'ho sforato?'). Default: mese corrente."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "anno": {"type": "integer", "description": "Anno (default: corrente)"},
                "mese": {"type": "integer", "description": "Mese 1-12 (default: corrente)"},
            },
        },
    },
    {
        "name": "set_obiettivo",
        "description": (
            "Crea o aggiorna un salvadanaio / obiettivo di risparmio (es. 'voglio "
            "mettere da parte 1000 euro per le vacanze entro agosto'). Definisce "
            "l'importo target e una scadenza opzionale. Non modifica quanto già "
            "accantonato. Per versare usa accantona."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {"type": "string", "description": "Nome obiettivo (es. 'vacanze', 'auto nuova')"},
                "target": {"type": "number", "description": "Importo da raggiungere in €"},
                "scadenza": {"type": "string", "description": "Data obiettivo YYYY-MM-DD (opzionale)"},
            },
            "required": ["nome", "target"],
        },
    },
    {
        "name": "accantona",
        "description": (
            "Versa (o ritira con importo negativo) denaro in un salvadanaio esistente "
            "(es. 'ho messo 50 euro nelle vacanze'). Aggiorna quanto accantonato."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nome": {"type": "string", "description": "Nome obiettivo"},
                "importo": {"type": "number", "description": "Importo da versare (negativo per ritirare)"},
            },
            "required": ["nome", "importo"],
        },
    },
    {
        "name": "get_obiettivi",
        "description": (
            "Mostra i salvadanai/obiettivi di risparmio: quanto accantonato vs target, "
            "percentuale, residuo, mesi rimanenti e quota mensile suggerita per "
            "arrivare in tempo. Usa quando l'utente chiede a che punto sono i risparmi "
            "('quanto manca per le vacanze?', 'come vanno i salvadanai?')."
        ),
        "input_schema": {"type": "object", "properties": {}},
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
    {
        "name": "gestisci_conti",
        "description": (
            "Gestione conti (CRUD). Usa quando l'utente vuole vedere/creare/modificare/"
            "disattivare/eliminare un conto (es. 'aggiungi conto Revolut di Marina', "
            "'disattiva paypal marina', 'cambia saldo iniziale del bancoposta a 500', "
            "'che conti ho?'). Un conto con transazioni non si elimina: disattivalo."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "azione": {"type": "string", "enum": ["lista", "crea", "modifica", "disattiva", "riattiva", "elimina"]},
                "nome": {"type": "string", "description": "Chiave conto (es. revolut_marina, postepay_andrea)"},
                "tipo": {"type": "string", "description": "banca|carta|wallet|contanti (per crea/modifica)"},
                "intestatario": {"type": "string", "description": "andrea|marina|famiglia (per crea/modifica)"},
                "saldo_iniziale": {"type": "number", "description": "Saldo iniziale € (per crea/modifica)"},
                "nuovo_nome": {"type": "string", "description": "Nuovo nome conto (per modifica/rinomina)"},
            },
            "required": ["azione"],
        },
    },
    {
        "name": "gestisci_transazioni",
        "description": (
            "Gestione movimenti (lista/modifica/elimina). Usa per correggere o "
            "cancellare una transazione già registrata (es. 'elimina l'ultima spesa', "
            "'correggi la spesa #12 a 25 euro', 'mostrami le ultime spese'). "
            "Per CREARE un movimento usa invece add_transazione."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "azione": {"type": "string", "enum": ["lista", "modifica", "elimina"]},
                "id": {"type": "integer", "description": "ID transazione (per modifica/elimina)"},
                "conto": {"type": "string", "description": "Filtro/nuovo conto"},
                "categoria": {"type": "string", "description": "Filtro/nuova categoria"},
                "importo": {"type": "number", "description": "Nuovo importo firmato (per modifica)"},
                "data": {"type": "string", "description": "Filtro/nuova data YYYY-MM-DD"},
                "data_da": {"type": "string", "description": "Lista: inizio periodo YYYY-MM-DD"},
                "data_a": {"type": "string", "description": "Lista: fine periodo YYYY-MM-DD"},
                "descrizione": {"type": "string", "description": "Nuova descrizione (per modifica)"},
                "limit": {"type": "integer", "description": "Lista: max righe (default 20)"},
            },
            "required": ["azione"],
        },
    },
    {
        "name": "gestisci_obiettivi",
        "description": (
            "Gestione salvadanai oltre a crea/versa: usa per ELIMINARLI o vederli. "
            "(Per crearli/aggiornarli usa set_obiettivo; per versare usa accantona.)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "azione": {"type": "string", "enum": ["lista", "elimina"]},
                "nome": {"type": "string", "description": "Nome obiettivo (per elimina)"},
            },
            "required": ["azione"],
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
        return await _add_transazione(inputs, user_id)
    if name == "get_saldo":
        return await _get_saldo(inputs, user_id)
    if name == "riepilogo_spese":
        return await _riepilogo_spese(inputs, user_id)
    if name == "gestisci_categorie":
        return await _gestisci_categorie(inputs)
    if name == "reset_economia":
        return await _reset_economia(inputs)
    if name == "set_budget":
        return await _set_budget(inputs)
    if name == "get_budget_status":
        return await _get_budget_status(inputs)
    if name == "set_obiettivo":
        return await _set_obiettivo(inputs)
    if name == "accantona":
        return await _accantona(inputs)
    if name == "get_obiettivi":
        return await _get_obiettivi(inputs)
    if name == "gestisci_conti":
        return await _gestisci_conti(inputs, user_id)
    if name == "gestisci_transazioni":
        return await _gestisci_transazioni(inputs, user_id)
    if name == "gestisci_obiettivi":
        return await _gestisci_obiettivi(inputs)
    return f"Tool sconosciuto nel modulo {NAME}: {name}"


async def _gestisci_conti(inputs: dict, user_id: str = "") -> str:
    azione = (inputs.get("azione") or "").strip().lower()
    if azione == "lista":
        return json.dumps({"ok": True, "conti": await list_conti(solo_attivi=False)},
                          ensure_ascii=False)
    nome = (inputs.get("nome") or "").strip().lower()
    if azione == "crea":
        if not nome:
            return "Nome conto mancante."
        tipo = (inputs.get("tipo") or "altro").strip().lower()
        intest = (inputs.get("intestatario") or "famiglia").strip().lower()
        saldo = inputs.get("saldo_iniziale")
        cid = await add_conto(nome, tipo, float(saldo) if saldo is not None else 0.0, intest)
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": True, "azione": "crea", "nome": nome, "id": cid}, ensure_ascii=False)
    if not nome:
        return "Nome conto mancante."
    if azione == "disattiva":
        ok = await update_conto(nome, attivo=False)
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": ok, "azione": "disattiva", "nome": nome}, ensure_ascii=False)
    if azione == "riattiva":
        ok = await update_conto(nome, attivo=True)
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": ok, "azione": "riattiva", "nome": nome}, ensure_ascii=False)
    if azione == "modifica":
        kw = {}
        if inputs.get("tipo"): kw["tipo"] = inputs["tipo"].strip().lower()
        if inputs.get("intestatario"): kw["intestatario"] = inputs["intestatario"].strip().lower()
        if inputs.get("saldo_iniziale") is not None: kw["saldo_iniziale"] = float(inputs["saldo_iniziale"])
        if inputs.get("nuovo_nome"): kw["nuovo_nome"] = inputs["nuovo_nome"].strip().lower()
        if not kw:
            return "Niente da modificare (passa tipo/intestatario/saldo_iniziale/nuovo_nome)."
        ok = await update_conto(nome, **kw)
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": ok, "azione": "modifica", "nome": nome, "campi": list(kw)}, ensure_ascii=False)
    if azione == "elimina":
        res = await delete_conto(nome)
        mqtt_pub.request_economia_refresh()
        if res.get("in_uso"):
            return (f"Conto '{nome}' ha {res['transazioni']} transazioni: non lo elimino. "
                    "Disattivalo invece (azione disattiva).")
        if not res.get("trovato", True) and not res.get("ok"):
            return f"Conto '{nome}' non trovato."
        return json.dumps({"ok": True, "azione": "elimina", "nome": nome}, ensure_ascii=False)
    return "Azione non valida: lista/crea/modifica/disattiva/riattiva/elimina."


async def _gestisci_transazioni(inputs: dict, user_id: str = "") -> str:
    azione = (inputs.get("azione") or "").strip().lower()
    membro = _membro_from_user(user_id)
    if azione == "lista":
        conto = await _resolve_conto(inputs["conto"], membro) if inputs.get("conto") else None
        righe = await list_transazioni(
            conto=conto,
            data_da=(inputs.get("data_da") or None),
            data_a=(inputs.get("data_a") or None),
            categoria=(inputs.get("categoria") or None),
            limit=int(inputs.get("limit") or 20),
        )
        return json.dumps({"ok": True, "transazioni": righe}, ensure_ascii=False)
    tid = inputs.get("id")
    if tid is None:
        return "Serve l'id della transazione."
    if azione == "elimina":
        ok = await delete_transazione(int(tid))
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": ok, "azione": "elimina", "id": tid}, ensure_ascii=False)
    if azione == "modifica":
        kw = {}
        if inputs.get("data"):
            d = _valida_data(inputs["data"])
            if d is None:
                return "Data non valida: usa il formato YYYY-MM-DD."
            kw["data"] = d
        if inputs.get("importo") is not None: kw["importo"] = float(inputs["importo"])
        if inputs.get("categoria"): kw["categoria"] = await normalize_categoria(inputs["categoria"])
        if inputs.get("descrizione") is not None: kw["descrizione"] = inputs["descrizione"].strip()
        if inputs.get("conto"):
            c = await _resolve_conto(inputs["conto"], membro)
            if c is None:
                return f"Conto '{inputs['conto']}' non riconosciuto."
            kw["conto"] = c
        if not kw:
            return "Niente da modificare."
        ok = await update_transazione(int(tid), **kw)
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": ok, "azione": "modifica", "id": tid, "campi": list(kw)}, ensure_ascii=False)
    return "Azione non valida: lista/modifica/elimina."


async def _gestisci_obiettivi(inputs: dict) -> str:
    azione = (inputs.get("azione") or "").strip().lower()
    if azione == "lista":
        return await _get_obiettivi(inputs)
    if azione == "elimina":
        nome = (inputs.get("nome") or "").strip()
        if not nome:
            return "Nome obiettivo mancante."
        ok = await delete_obiettivo(nome)
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": ok, "azione": "elimina", "nome": nome}, ensure_ascii=False)
    return "Azione non valida: lista/elimina."


async def _set_obiettivo(inputs: dict) -> str:
    nome = (inputs.get("nome") or "").strip()
    if not nome:
        return "Nome obiettivo mancante."
    try:
        target = float(inputs["target"])
    except (KeyError, ValueError, TypeError):
        return "Target mancante o non valido."
    if target <= 0:
        return "Il target deve essere positivo."
    scadenza = (inputs.get("scadenza") or "").strip() or None
    if scadenza:
        try:
            date.fromisoformat(scadenza)
        except ValueError:
            return "Scadenza non valida (usa YYYY-MM-DD)."
    await set_obiettivo(nome, target, scadenza)
    mqtt_pub.request_economia_refresh()
    return json.dumps({"ok": True, "nome": nome, "target": round(target, 2),
                       "scadenza": scadenza}, ensure_ascii=False)


async def _accantona(inputs: dict) -> str:
    nome = (inputs.get("nome") or "").strip()
    if not nome:
        return "Nome obiettivo mancante."
    try:
        importo = float(inputs["importo"])
    except (KeyError, ValueError, TypeError):
        return "Importo mancante o non valido."
    res = await accantona(nome, importo)
    if res is None:
        return f"Obiettivo '{nome}' non trovato. Crealo prima con set_obiettivo."
    mqtt_pub.request_economia_refresh()
    res["ok"] = True
    return json.dumps(res, ensure_ascii=False)


async def _get_obiettivi(inputs: dict) -> str:
    obs = await get_obiettivi()
    if not obs:
        return json.dumps({"ok": True, "nessun_obiettivo": True,
                           "msg": "Nessun salvadanaio. Creane uno con set_obiettivo."},
                          ensure_ascii=False)
    return json.dumps({"ok": True, "obiettivi": obs}, ensure_ascii=False)


async def _set_budget(inputs: dict) -> str:
    categoria = (inputs.get("categoria") or "").strip()
    if not categoria:
        return "Categoria mancante."
    try:
        importo = float(inputs["importo"])
    except (KeyError, ValueError, TypeError):
        return "Importo mancante o non valido."
    if importo <= 0:
        ok = await delete_budget(categoria)
        mqtt_pub.request_economia_refresh()
        return json.dumps({"ok": True, "azione": "rimosso", "categoria": categoria.lower(),
                           "trovato": ok}, ensure_ascii=False)
    cat = await set_budget(categoria, importo)
    mqtt_pub.request_economia_refresh()
    return json.dumps({"ok": True, "azione": "impostato", "categoria": cat,
                       "budget": round(importo, 2)}, ensure_ascii=False)


async def _get_budget_status(inputs: dict) -> str:
    today = date.today()
    try:
        anno = int(inputs.get("anno") or today.year)
        mese = int(inputs.get("mese") or today.month)
    except (ValueError, TypeError):
        return "Anno/mese non validi."
    if not (1 <= mese <= 12):
        return "Mese non valido (1-12)."
    status = await get_budget_status(anno, mese)
    if not status:
        return json.dumps({"ok": True, "nessun_budget": True,
                           "msg": "Nessun budget impostato. Usa set_budget per crearne."},
                          ensure_ascii=False)
    return json.dumps({"ok": True, "anno": anno, "mese": mese, "budget": status},
                      ensure_ascii=False)


async def _reset_economia(inputs: dict) -> str:
    reset_categorie = bool(inputs.get("reset_categorie", False))
    reset_saldi = bool(inputs.get("reset_saldi", False))
    if not inputs.get("confirm", False):
        # anteprima: mostra i saldi attuali senza cancellare nulla
        saldi = await get_saldi()
        return json.dumps({
            "ok": False,
            "conferma_richiesta": True,
            "msg": ("Operazione DISTRUTTIVA: cancella tutte le transazioni, "
                    "tutti i budget e tutti i salvadanai/obiettivi"
                    + (", ripristina le categorie di default" if reset_categorie else "")
                    + (", azzera i saldi iniziali" if reset_saldi else "")
                    + ". Mostra all'utente cosa verrà rimosso e chiedi conferma "
                      "esplicita; poi richiama reset_economia con confirm=true."),
            "saldi_attuali": saldi,
            "obiettivi_attuali": await get_obiettivi(),
        }, ensure_ascii=False)
    res = await reset_economia(reset_categorie=reset_categorie, reset_saldi=reset_saldi)
    mqtt_pub.request_economia_refresh()
    res["ok"] = True
    return json.dumps(res, ensure_ascii=False)


async def _gestisci_categorie(inputs: dict) -> str:
    azione = (inputs.get("azione") or "").strip().lower()
    if azione == "lista":
        cats = await list_categorie()
        return json.dumps({"ok": True, "categorie": cats}, ensure_ascii=False)
    if azione == "crea":
        nome = (inputs.get("nome") or "").strip()
        if not nome:
            return "Nome categoria mancante."
        canon = await normalize_categoria(nome)
        return json.dumps({"ok": True, "azione": "crea", "categoria": canon}, ensure_ascii=False)
    if azione == "elimina":
        nome = (inputs.get("nome") or "").strip()
        if not nome:
            return "Nome categoria mancante."
        res = await delete_categoria(nome)
        if res.get("in_uso"):
            return (f"Categoria '{nome}' usata da {res['transazioni']} transazioni: "
                    "non la elimino. Uniscila a un'altra (azione unisci) prima.")
        if not res.get("ok"):
            return f"Categoria '{nome}' non trovata."
        return json.dumps({"ok": True, "azione": "elimina", "categoria": nome.lower()}, ensure_ascii=False)
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


async def _get_saldo(inputs: dict, user_id: str = "") -> str:
    membro = _membro_from_user(user_id)
    conto_raw = (inputs.get("conto") or "").strip()
    if conto_raw:
        conto = await _resolve_conto(conto_raw, membro)
        if conto is None:
            return f"Conto '{conto_raw}' non riconosciuto. Conti disponibili: {_CONTI_DESC}."
        saldo = await get_saldo(conto)
        return json.dumps({"ok": True, "conto": conto, "saldo": saldo}, ensure_ascii=False)
    saldi = await get_saldi()
    totale = round(sum(s["saldo"] for s in saldi), 2)
    per_membro = {}
    for s in saldi:
        per_membro[s["intestatario"]] = round(per_membro.get(s["intestatario"], 0.0) + s["saldo"], 2)
    return json.dumps({"ok": True, "saldi": saldi, "per_intestatario": per_membro,
                       "totale": totale}, ensure_ascii=False)


async def _riepilogo_spese(inputs: dict, user_id: str = "") -> str:
    membro = _membro_from_user(user_id)
    conto_raw = (inputs.get("conto") or "").strip()
    conto = None
    if conto_raw:
        conto = await _resolve_conto(conto_raw, membro)
        if conto is None:
            return f"Conto '{conto_raw}' non riconosciuto. Conti disponibili: {_CONTI_DESC}."
    intest = (inputs.get("intestatario") or "").strip().lower() or None
    if intest and intest not in econ_def.MEMBRI and intest != "famiglia":
        return f"Intestatario '{intest}' non valido. Membri: {', '.join(econ_def.MEMBRI)}, famiglia."
    data_da = (inputs.get("data_da") or "").strip() or None
    data_a = (inputs.get("data_a") or "").strip() or None
    rep = await riepilogo_spese(data_da=data_da, data_a=data_a, conto=conto, intestatario=intest)
    rep["ok"] = True
    rep["conto"] = conto or "tutti"
    rep["intestatario"] = intest or "tutti"
    rep["periodo"] = {"da": data_da or "inizio", "a": data_a or "oggi"}
    return json.dumps(rep, ensure_ascii=False)


async def _add_transazione(inputs: dict, user_id: str = "") -> str:
    membro = _membro_from_user(user_id)
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
    # default: contanti del membro che scrive; altrimenti risolvi col contesto membro
    conto = await _resolve_conto(conto_raw, membro) if conto_raw else econ_def.conto_per("contanti", membro)
    if conto is None:
        return f"Conto '{conto_raw}' non riconosciuto. Conti disponibili: {_CONTI_DESC}."

    data_raw = (inputs.get("data") or "").strip()
    if data_raw:
        data = _valida_data(data_raw)
        if data is None:
            return "Data non valida: usa il formato YYYY-MM-DD."
    else:
        data = date.today().isoformat()

    # normalizza categoria: riusa una canonica esistente (case-insensitive),
    # altrimenti la registra. Evita doppioni tipo "Cibo"/"cibo"/"alimentari".
    categoria = await normalize_categoria(categoria)

    importo_firmato = importo if tipo == "entrata" else -importo
    await add_transazione(conto, data, importo_firmato, categoria, descrizione)
    mqtt_pub.request_economia_refresh()
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


async def import_estratto_bytes(content: bytes, filename: str, caption: str,
                                user_id: str) -> str:
    """Importa un estratto conto (xlsx/csv) inviato come documento. Riconosce
    formato BancoPosta/Postepay, sceglie il conto (Postepay = per membro dedotto
    da caption o dal parlante), inserisce con dedup. Ritorna testo per l'utente."""
    try:
        rows = econ_import.rows_from_file(content, filename)
    except Exception as e:
        return f"Non riesco a leggere il file ({filename}): {e}"
    res = econ_import.parse(rows)
    if res.get("formato") is None:
        return ("File non riconosciuto come estratto BancoPosta o Postepay. "
                + (res.get("errore") or ""))
    formato = res["formato"]
    movimenti = res["movimenti"]
    if not movimenti:
        return f"Estratto {formato} riconosciuto ma nessun movimento valido trovato."

    if formato == "bancoposta":
        conto = "bancoposta"
    else:  # postepay: deduci il membro
        cap = (caption or "").lower()
        membro = next((m for m in econ_def.MEMBRI if m in cap), None) or _membro_from_user(user_id)
        conto = econ_def.conto_per("postepay", membro) or f"postepay_{membro}"
        if conto not in econ_def.CONTI:
            return (f"Non so a quale PostePay assegnare l'estratto. Specifica il membro "
                    f"nella didascalia (es. '{econ_def.MEMBRI[0]}').")

    out = await import_transazioni(conto, movimenti)
    mqtt_pub.request_economia_refresh()
    saldo = await get_saldo(conto)
    label = econ_def.label(conto)
    scartate = res.get("scartate", 0)
    msg = (f"📥 Import {formato} → {label}\n"
           f"✅ {out['inserite']} movimenti importati\n")
    if out["duplicate"]:
        msg += f"♻️ {out['duplicate']} già presenti (saltati)\n"
    if scartate:
        msg += f"⚠️ {scartate} righe scartate (date/importi non validi)\n"
    msg += f"💰 Saldo {label}: €{saldo:.2f}"
    return msg


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
    """Inietta categorie + conti esistenti così Claude riusa categorie (anti-doppioni)
    e sceglie il conto giusto per intestatario."""
    out = []
    cats = _categorie_sync()
    if cats:
        out.append("\n- ECONOMIA categorie esistenti (riusa queste quando registri "
                   "spese/entrate, non crearne di simili: se serve una nuova davvero "
                   "diversa va bene): " + ", ".join(cats) + ".")
    conti = ", ".join(f"{k} ({d['intestatario']})" for k, d in econ_def.CONTI.items())
    out.append("\n- ECONOMIA conti (chiave → intestatario): " + conti + ". BancoPosta è "
               "cointestato (famiglia); PostePay/PayPal/Contanti sono per persona. Quando "
               "registri un movimento usa la chiave conto corretta; se l'utente non indica "
               "il conto si usano i suoi contanti. Per i riepiloghi puoi filtrare per "
               "'intestatario' (andrea/marina/famiglia) oltre che per conto.")
    return "".join(out)
