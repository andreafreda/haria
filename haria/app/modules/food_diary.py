"""Modulo food_diary: profili nutrizionali, peso/BMI, log pasti.

Fase 0 (onboarding profili) + Fase 1 (log pasti). La stima nutrizionale
dei pasti la fornisce Claude stesso negli input di log_meal (grammi, kcal,
macro), così non serve una seconda chiamata API. BMI e fabbisogno calorico
sono calcolati in Python (deterministici).
"""
import json
import os
import glob
from datetime import datetime
from memory import (
    get_profile, upsert_profile, add_weight, get_weight_history,
    add_meal, get_meals, set_plan_meal, get_meal_plan,
    get_day_totals, add_hydration, get_hydration_day,
    add_shopping_items, get_shopping_list, check_shopping_item, clear_shopping_list,
    add_pantry_items, get_pantry, get_pantry_expiring, consume_pantry_item, clear_pantry,
)
import nutrition
from ha_client import get_states, call_service

NAME = "food_diary"


def _today() -> str:
    return datetime.now().date().isoformat()

ACTIVITY_FACTORS = {
    "sedentario": 1.2,
    "leggero": 1.375,
    "moderato": 1.55,
    "attivo": 1.725,
    "molto_attivo": 1.9,
}


def _norm(member: str) -> str:
    return (member or "").strip().lower()


# Diete di riferimento ("spunto"): caricate da repo (app/diets) + cartella
# persistente FTP-accessibile (/config/haria_diets). Estendibile senza codice:
# basta aggiungere file .md/.txt in /config/haria_diets.
_DIET_WRITE_DIR = os.environ.get("DIETS_PATH", "/config/haria_diets")
_DIET_DIRS = [
    os.path.join(os.path.dirname(__file__), "..", "diets"),
    _DIET_WRITE_DIR,
]
_DIET_EXTS = ("*.md", "*.txt")


def _slugify(s: str) -> str:
    import re
    s = (s or "dieta").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "dieta"


def load_diets() -> str:
    """Concatena il contenuto di tutte le diete di riferimento trovate."""
    parts: list[str] = []
    seen: set[str] = set()
    for d in _DIET_DIRS:
        if not d or not os.path.isdir(d):
            continue
        files = sorted(f for ext in _DIET_EXTS for f in glob.glob(os.path.join(d, ext)))
        for f in files:
            base = os.path.basename(f)
            if base in seen:
                continue
            seen.add(base)
            try:
                with open(f, encoding="utf-8") as fh:
                    txt = fh.read().strip()
                if txt:
                    parts.append(f"### {base}\n{txt}")
            except OSError:
                continue
    return "\n\n".join(parts)


def diet_prompt() -> str:
    """Frammento system prompt con le diete di riferimento (vuoto se assenti)."""
    diets = load_diets()
    if not diets:
        return ""
    return (
        "\n\n=== DIETE DI RIFERIMENTO (spunto, non vincolo) ===\n"
        "Le seguenti diete passate dell'utente servono da ISPIRAZIONE per proporre "
        "e variare i piani pasti: rispettane stile, regole, frequenze e porzioni "
        "quando generi menù con plan_week/set_plan_meal. Non sono un piano attivo "
        "rigido salvo richiesta esplicita.\n\n" + diets
    )


# Esposto al registry: contenuto diete iniettato a ogni messaggio (così le
# diete aggiunte a runtime via save_diet sono subito attive, senza riavvio).
dynamic_prompt = diet_prompt


def compute_bmi(weight_kg: float, height_cm: float) -> float | None:
    if not weight_kg or not height_cm:
        return None
    h = height_cm / 100.0
    return round(weight_kg / (h * h), 1)


def bmi_category(bmi: float | None) -> str:
    if bmi is None:
        return "n/d"
    if bmi < 18.5:
        return "sottopeso"
    if bmi < 25:
        return "normopeso"
    if bmi < 30:
        return "sovrappeso"
    return "obesità"


def compute_kcal_target(sex: str, age: int, height_cm: float, weight_kg: float,
                        activity_level: str, goal: str) -> int | None:
    """Mifflin-St Jeor BMR * fattore attività, aggiustato per obiettivo."""
    if not all([sex, age, height_cm, weight_kg]):
        return None
    s = 5 if str(sex).lower().startswith("m") else -161
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + s
    factor = ACTIVITY_FACTORS.get(str(activity_level).lower(), 1.375)
    tdee = bmr * factor
    g = str(goal or "").lower()
    if "dimagr" in g or "perd" in g:
        tdee -= 500
    elif "aument" in g or "massa" in g:
        tdee += 300
    return int(round(tdee))


def compute_macro_targets(kcal_target: int | None, weight_kg: float | None) -> dict | None:
    """Ripartizione macro dal target kcal.

    Proteine 1.6 g/kg (fallback 20% kcal se peso assente); grassi 25% kcal;
    carboidrati il resto. Ritorna grammi + % di ogni macro.
    """
    if not kcal_target:
        return None
    if weight_kg:
        protein_g = round(1.6 * weight_kg, 1)
    else:
        protein_g = round((0.20 * kcal_target) / 4, 1)
    protein_kcal = protein_g * 4
    fat_kcal = 0.25 * kcal_target
    fat_g = round(fat_kcal / 9, 1)
    carbs_kcal = kcal_target - protein_kcal - fat_kcal
    if carbs_kcal < 0:
        carbs_kcal = 0
    carbs_g = round(carbs_kcal / 4, 1)
    pct = lambda k: round(100 * k / kcal_target, 1) if kcal_target else None
    return {
        "protein_target_g": protein_g,
        "carbs_target_g": carbs_g,
        "fat_target_g": fat_g,
        "protein_pct": pct(protein_kcal),
        "carbs_pct": pct(carbs_kcal),
        "fat_pct": pct(fat_kcal),
    }


TOOLS = [
    {
        "name": "set_diet_profile",
        "description": (
            "Imposta o aggiorna il profilo nutrizionale di un membro famiglia (onboarding o modifica). "
            "Passa solo i campi noti; gli altri restano invariati. Calcola automaticamente BMI e fabbisogno calorico."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string", "description": "Nome membro (es. Andrea, Marina, bimba). Se non indicato usa l'utente corrente."},
                "sex": {"type": "string", "enum": ["m", "f"], "description": "Sesso biologico"},
                "age": {"type": "integer"},
                "height_cm": {"type": "number"},
                "weight_kg": {"type": "number"},
                "goal": {"type": "string", "description": "es. mantenere, dimagrire, aumentare massa"},
                "activity_level": {"type": "string", "enum": list(ACTIVITY_FACTORS.keys())},
                "allergies": {"type": "string", "description": "Allergie/intolleranze, testo libero"},
                "preferences": {"type": "string", "description": "Cibi amati/odiati"},
                "restrictions": {"type": "string", "description": "Restrizioni dietetiche"},
            },
            "required": ["member"],
        },
    },
    {
        "name": "get_diet_profile",
        "description": "Leggi il profilo nutrizionale di un membro (dati, BMI, fabbisogno calorico).",
        "input_schema": {
            "type": "object",
            "properties": {"member": {"type": "string"}},
            "required": ["member"],
        },
    },
    {
        "name": "log_weight",
        "description": "Registra il peso attuale di un membro. Aggiorna BMI e profilo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string"},
                "weight_kg": {"type": "number"},
            },
            "required": ["member", "weight_kg"],
        },
    },
    {
        "name": "get_weight_history",
        "description": "Storico peso/BMI di un membro (trend).",
        "input_schema": {
            "type": "object",
            "properties": {"member": {"type": "string"}},
            "required": ["member"],
        },
    },
    {
        "name": "log_meal",
        "description": (
            "Registra un pasto mangiato. STIMA TU i valori nutrizionali: per ogni alimento indica grammi, kcal e macro, "
            "e calcola i totali. Usa porzioni realistiche se l'utente non specifica le quantità."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string", "description": "Chi ha mangiato. Se non indicato usa l'utente corrente."},
                "meal_type": {"type": "string", "enum": ["colazione", "pranzo", "cena", "snack"]},
                "description": {"type": "string", "description": "Descrizione del pasto"},
                "items": {
                    "type": "array",
                    "description": "Alimenti con stima nutrizionale",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "grams": {"type": "number"},
                            "kcal": {"type": "number"},
                            "protein_g": {"type": "number"},
                            "carbs_g": {"type": "number"},
                            "fat_g": {"type": "number"},
                        },
                        "required": ["name", "grams", "kcal"],
                    },
                },
                "kcal_total": {"type": "number"},
                "protein_g": {"type": "number"},
                "carbs_g": {"type": "number"},
                "fat_g": {"type": "number"},
                "eaten_at": {"type": "string", "description": "ISO datetime se pasto passato; altrimenti ometti (= ora)"},
            },
            "required": ["member", "meal_type", "description", "kcal_total"],
        },
    },
    {
        "name": "get_meals",
        "description": "Elenca i pasti registrati di un membro, opzionalmente in un intervallo di date (ISO).",
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string"},
                "date_from": {"type": "string", "description": "ISO date/datetime inizio"},
                "date_to": {"type": "string", "description": "ISO date/datetime fine"},
            },
            "required": ["member"],
        },
    },
    {
        "name": "plan_week",
        "description": (
            "Crea o rigenera il piano pasti settimanale della famiglia. GENERA TU il menù tenendo conto di "
            "profili, obiettivi dieta, allergie, preferenze e varietà (no ripetizioni). Fornisci una voce per "
            "ogni pasto pianificato con data ISO (YYYY-MM-DD), tipo pasto e portate."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "meals": {
                    "type": "array",
                    "description": "Pasti del piano settimanale",
                    "items": {
                        "type": "object",
                        "properties": {
                            "date": {"type": "string", "description": "Data ISO YYYY-MM-DD"},
                            "meal_type": {"type": "string", "enum": ["colazione", "pranzo", "cena", "snack"]},
                            "items": {"type": "string", "description": "Cosa si mangia (portate)"},
                            "recipe": {"type": "string", "description": "Ricetta breve opzionale"},
                            "servings": {"type": "integer", "description": "Numero porzioni/persone"},
                            "kcal": {"type": "number", "description": "kcal stimate del pasto a porzione"},
                            "member": {"type": "string", "description": "Ometti per pasto comune; valorizza per override personale"},
                        },
                        "required": ["date", "meal_type", "items"],
                    },
                },
            },
            "required": ["meals"],
        },
    },
    {
        "name": "get_meal_plan",
        "description": "Leggi il piano pasti in un intervallo di date ISO (YYYY-MM-DD). Usa per 'cosa si mangia oggi/questa settimana'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Data ISO inizio (YYYY-MM-DD)"},
                "date_to": {"type": "string", "description": "Data ISO fine (YYYY-MM-DD)"},
            },
            "required": ["date_from", "date_to"],
        },
    },
    {
        "name": "set_plan_meal",
        "description": (
            "Fissa o sostituisce UN pasto nel piano. Senza 'member' = piano COMUNE (vale per tutti). "
            "Con 'member' = override PERSONALE solo per quella persona (es. Marina mangia altro a pranzo). "
            "Sovrascrive il pasto esistente per quella data+tipo+membro."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Data ISO YYYY-MM-DD"},
                "meal_type": {"type": "string", "enum": ["colazione", "pranzo", "cena", "snack"]},
                "items": {"type": "string", "description": "Cosa si mangia"},
                "recipe": {"type": "string"},
                "servings": {"type": "integer"},
                "kcal": {"type": "number", "description": "kcal stimate del pasto a porzione"},
                "member": {"type": "string", "description": "Nome persona per override personale; ometti per piano comune"},
            },
            "required": ["date", "meal_type", "items"],
        },
    },
    {
        "name": "get_daily_summary",
        "description": (
            "Riepilogo nutrizionale giornaliero di un membro: kcal e macro consumati vs fabbisogno (kcal_target), "
            "calorie rimanenti, idratazione. Usa per 'quanto ho mangiato oggi', 'quante calorie mi restano'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string"},
                "date": {"type": "string", "description": "Giorno YYYY-MM-DD; default oggi"},
            },
            "required": ["member"],
        },
    },
    {
        "name": "log_hydration",
        "description": "Registra acqua/liquidi bevuti (ml) da un membro. Stima ml se l'utente dice 'un bicchiere' (~250ml) o 'una bottiglia' (~500ml).",
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string"},
                "ml": {"type": "number", "description": "Millilitri bevuti"},
            },
            "required": ["member", "ml"],
        },
    },
    {
        "name": "get_hydration",
        "description": "Quanta acqua ha bevuto oggi (o in una data) un membro.",
        "input_schema": {
            "type": "object",
            "properties": {
                "member": {"type": "string"},
                "date": {"type": "string", "description": "Giorno YYYY-MM-DD; default oggi"},
            },
            "required": ["member"],
        },
    },
    {
        "name": "lookup_nutrition",
        "description": (
            "Cerca valori nutrizionali REALI per 100 g di un alimento da fonti gratuite (OpenFoodFacts/USDA), con cache. "
            "Usa per avere kcal/macro accurati invece di stimarli a memoria. Restituisce null se nessuna fonte risponde (allora stima tu)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Nome alimento (es. 'petto di pollo', 'banana')"}},
            "required": ["query"],
        },
    },
    {
        "name": "lookup_barcode",
        "description": "Cerca un prodotto confezionato dal codice a barre (EAN) su OpenFoodFacts: nome, marca, valori per 100 g.",
        "input_schema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Codice a barre / EAN"}},
            "required": ["code"],
        },
    },
    {
        "name": "add_shopping_items",
        "description": (
            "Aggiunge voci alla lista della spesa. Usa per generare la spesa dal piano settimanale: "
            "GENERA TU gli ingredienti aggregati (quantità per la famiglia) dai pasti pianificati."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "qty": {"type": "string", "description": "Quantità (es. '500 g', '2 pz')"},
                            "category": {"type": "string", "description": "Reparto (es. verdura, carne, dispensa)"},
                        },
                        "required": ["name"],
                    },
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "get_shopping_list",
        "description": "Leggi la lista della spesa attuale (voci non ancora prese, salvo include_checked).",
        "input_schema": {
            "type": "object",
            "properties": {"include_checked": {"type": "boolean"}},
        },
    },
    {
        "name": "check_shopping_item",
        "description": "Segna come preso un articolo della lista spesa (per nome, match parziale).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "clear_shopping_list",
        "description": "Svuota la lista spesa. only_checked=true rimuove solo gli articoli già presi.",
        "input_schema": {
            "type": "object",
            "properties": {"only_checked": {"type": "boolean"}},
        },
    },
    {
        "name": "sync_shopping_to_ha",
        "description": (
            "Copia la lista della spesa interna in una lista todo nativa di Home Assistant "
            "(visibile in app e dashboard HA). Usa quando l'utente vuole la spesa 'sul telefono'/in HA."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "list": {"type": "string", "description": "Nome o entity_id lista todo HA; default prima disponibile"},
            },
        },
    },
    {
        "name": "add_pantry_items",
        "description": (
            "Aggiungi prodotti alla DISPENSA/SCORTE di casa (anti-spreco). Usa quando l'utente dice "
            "cosa ha in casa o dopo aver fatto la spesa. Indica scadenza se nota."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "qty": {"type": "string", "description": "Quantità es. '500 g', '2 pz'"},
                            "category": {"type": "string", "description": "Categoria es. dispensa, frigo, freezer"},
                            "expires_on": {"type": "string", "description": "Scadenza ISO YYYY-MM-DD se nota"},
                        },
                        "required": ["name"],
                    },
                },
            },
            "required": ["items"],
        },
    },
    {
        "name": "get_pantry",
        "description": "Leggi la dispensa/scorte. 'expiring=true' per le sole voci in scadenza entro N giorni.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string", "description": "Filtra per categoria"},
                "expiring": {"type": "boolean", "description": "Solo voci in scadenza"},
                "within_days": {"type": "integer", "description": "Giorni per 'expiring' (default 3)"},
            },
        },
    },
    {
        "name": "consume_pantry_item",
        "description": "Rimuovi/consuma una voce dalla dispensa per nome (quando finita o usata).",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "clear_pantry",
        "description": "Svuota completamente la dispensa.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "save_diet",
        "description": (
            "Salva una dieta di riferimento (spunto) in modo persistente ed estensibile. "
            "Usa dopo aver estratto il contenuto di un PDF/dieta inviato dall'utente. "
            "Il contenuto diventa subito disponibile come ispirazione per i piani pasto."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nome breve descrittivo (es. 'dieta agosto 2025')"},
                "content": {"type": "string", "description": "Contenuto sintetico in markdown: profilo, regole, frequenze, menù, porzioni, sostituzioni"},
            },
            "required": ["name", "content"],
        },
    },
    {
        "name": "list_diets",
        "description": "Elenca le diete di riferimento attualmente caricate (nomi file).",
        "input_schema": {"type": "object", "properties": {}},
    },
]

PROMPT = (
    "\n- DIARIO ALIMENTARE: per registrare pasti usa log_meal e STIMA tu grammi/kcal/macro di ogni alimento (porzioni realistiche)."
    " Per peso usa log_weight, per i profili set_diet_profile/get_diet_profile."
    " Se l'utente non indica il membro, usa il nome dell'utente corrente come 'member'."
    " Un utente può registrare per un altro membro (es. la bimba): in tal caso usa il nome del membro indicato."
    " Per query storiche usa get_meals/get_weight_history."
    "\n- PIANO SETTIMANALE: per 'cosa si mangia oggi/questa settimana' usa get_meal_plan (calcola le date ISO dalla data attuale)."
    " Se non esiste un piano, proponi di crearlo con plan_week: genera tu un menù vario tenendo conto di profili/dieta/allergie/preferenze della famiglia, e STIMA le kcal di ogni pasto pianificato."
    " Se l'utente vuole cambiare un pasto, PROPONI 2-3 alternative coerenti; quando sceglie, salva con set_plan_meal."
    " Il piano è COMUNE di default (set_plan_meal/plan_week senza 'member'). Se una persona mangia qualcosa di diverso, salva un OVERRIDE PERSONALE valorizzando 'member' (solo per quel pasto). get_meal_plan ritorna il campo 'member' (vuoto = comune)."
    "\n- VALORI NUTRIZIONALI: prima di stimare kcal/macro a memoria, prova lookup_nutrition per dati reali (cache locale)."
    " Per prodotti confezionati col codice a barre usa lookup_barcode. Se la fonte non risponde, stima tu."
    "\n- RIEPILOGO: per 'quanto ho mangiato/quante calorie restano' usa get_daily_summary: riporta sia kcal sia MACRO (proteine/carbo/grassi) consumati vs target e rimanenti."
    "\n- MACRO: ogni profilo ha target macro (macro_targets in get_diet_profile/get_daily_summary). Quando pianifichi/proponi pasti tieni conto del bilancio proteine/carbo/grassi, non solo delle kcal."
    "\n- SPESA: per generare la lista della spesa, leggi il piano (get_meal_plan), GENERA tu gli ingredienti aggregati e salvali con add_shopping_items."
    " Per consultarla usa get_shopping_list, per spuntare check_shopping_item, per svuotare clear_shopping_list."
    "\n- DISPENSA/SCORTE (anti-spreco): usa add_pantry_items per registrare cosa c'è in casa (con scadenza se nota), get_pantry per consultarla (expiring=true per voci in scadenza), consume_pantry_item quando un prodotto finisce."
    " Quando pianifichi i pasti o generi la spesa, TIENI CONTO di cosa è già in dispensa (evita di ricomprare) e privilegia gli ingredienti in scadenza."
    "\n- CONSIGLI: quando proponi cosa cucinare, tieni conto di profili/obiettivi/allergie e privilegia ricette semplici e veloci; offri sempre alternative."
    "\n- DIETE PDF: se l'utente manda un PDF di una dieta, ESTRAI il contenuto rilevante (profilo, regole, frequenze, menù, porzioni, sostituzioni) in forma sintetica e SALVALO con save_diet (name breve descrittivo, content in markdown). Conferma e, se richiesto, riadatta il piano (plan_week) sulla nuova dieta."
)


async def _recompute_profile_derived(member: str) -> dict | None:
    """Ricalcola BMI e kcal_target dal profilo corrente e li salva."""
    p = await get_profile(member)
    if not p:
        return None
    bmi = compute_bmi(p.get("weight_kg"), p.get("height_cm"))
    kcal = compute_kcal_target(
        p.get("sex"), p.get("age"), p.get("height_cm"),
        p.get("weight_kg"), p.get("activity_level"), p.get("goal"),
    )
    derived = {}
    if bmi is not None:
        derived["bmi"] = bmi
    if kcal is not None:
        derived["kcal_target"] = kcal
    if derived:
        await upsert_profile(member, derived)
    return await get_profile(member)


_MQTT_REFRESH_TOOLS = {
    "log_meal", "log_hydration", "plan_week", "set_plan_meal",
    "set_diet_profile", "log_weight", "add_shopping_items",
    "check_shopping_item", "clear_shopping_list",
    "add_pantry_items", "consume_pantry_item", "clear_pantry",
}


def _maybe_refresh_mqtt(name: str):
    if name not in _MQTT_REFRESH_TOOLS:
        return
    try:
        import mqtt_pub
        mqtt_pub.request_refresh()
    except Exception:
        pass


async def handle(name: str, inputs: dict, user_id: str) -> str:
    member = _norm(inputs.get("member"))
    _maybe_refresh_mqtt(name)

    if name == "set_diet_profile":
        if not member:
            return "Errore: specifica il membro."
        fields = {k: inputs[k] for k in (
            "sex", "age", "height_cm", "weight_kg", "goal",
            "activity_level", "allergies", "preferences", "restrictions",
        ) if k in inputs}
        await upsert_profile(member, fields)
        p = await _recompute_profile_derived(member)
        p["bmi_category"] = bmi_category(p.get("bmi"))
        return json.dumps({"ok": True, "profile": p}, ensure_ascii=False)

    if name == "get_diet_profile":
        p = await get_profile(member)
        if not p:
            return f"Nessun profilo per '{member}'. Usa set_diet_profile per crearlo."
        p["bmi_category"] = bmi_category(p.get("bmi"))
        macros = compute_macro_targets(p.get("kcal_target"), p.get("weight_kg"))
        if macros:
            p["macro_targets"] = macros
        return json.dumps(p, ensure_ascii=False)

    if name == "log_weight":
        weight = inputs["weight_kg"]
        p = await get_profile(member)
        bmi = compute_bmi(weight, p.get("height_cm")) if p else None
        await add_weight(member, weight, bmi)
        # aggiorna peso nel profilo + ricalcola derivati
        await upsert_profile(member, {"weight_kg": weight})
        await _recompute_profile_derived(member)
        return json.dumps(
            {"ok": True, "member": member, "weight_kg": weight,
             "bmi": bmi, "bmi_category": bmi_category(bmi)},
            ensure_ascii=False,
        )

    if name == "get_weight_history":
        hist = await get_weight_history(member)
        return json.dumps(hist, ensure_ascii=False) if hist else f"Nessun peso registrato per '{member}'."

    if name == "log_meal":
        totals = {
            "kcal_total": inputs.get("kcal_total"),
            "protein_g": inputs.get("protein_g"),
            "carbs_g": inputs.get("carbs_g"),
            "fat_g": inputs.get("fat_g"),
        }
        meal = await add_meal(
            member, inputs["meal_type"], inputs["description"],
            totals, inputs.get("items", []),
            inputs.get("eaten_at"), user_id,
        )
        return json.dumps({"ok": True, "meal": meal, "kcal": totals["kcal_total"]}, ensure_ascii=False)

    if name == "get_meals":
        meals = await get_meals(member, inputs.get("date_from"), inputs.get("date_to"))
        return json.dumps(meals, ensure_ascii=False) if meals else f"Nessun pasto registrato per '{member}'."

    if name == "plan_week":
        meals = inputs.get("meals", [])
        if not meals:
            return "Errore: nessun pasto fornito per il piano."
        for m in meals:
            await set_plan_meal(
                m["date"], m["meal_type"], m["items"],
                m.get("recipe"), m.get("servings"), m.get("kcal"), m.get("member"),
            )
        return json.dumps({"ok": True, "count": len(meals)}, ensure_ascii=False)

    if name == "get_meal_plan":
        plan = await get_meal_plan(inputs["date_from"], inputs["date_to"])
        return json.dumps(plan, ensure_ascii=False) if plan else "Nessun piano per le date richieste."

    if name == "set_plan_meal":
        await set_plan_meal(
            inputs["date"], inputs["meal_type"], inputs["items"],
            inputs.get("recipe"), inputs.get("servings"), inputs.get("kcal"),
            inputs.get("member"),
        )
        return json.dumps({"ok": True, "date": inputs["date"], "meal_type": inputs["meal_type"],
                           "member": inputs.get("member") or "comune"}, ensure_ascii=False)

    if name == "get_daily_summary":
        day = inputs.get("date") or _today()
        totals = await get_day_totals(member, day)
        hydr = await get_hydration_day(member, day)
        p = await get_profile(member)
        target = p.get("kcal_target") if p else None
        remaining = round(target - totals["kcal"], 1) if target else None
        macros = compute_macro_targets(target, p.get("weight_kg")) if p else None
        macro_remaining = None
        if macros:
            macro_remaining = {
                "protein_g": round(macros["protein_target_g"] - totals["protein_g"], 1),
                "carbs_g": round(macros["carbs_target_g"] - totals["carbs_g"], 1),
                "fat_g": round(macros["fat_target_g"] - totals["fat_g"], 1),
            }
        return json.dumps({
            "member": member, "date": day, "consumed": totals,
            "kcal_target": target, "kcal_remaining": remaining,
            "macro_targets": macros, "macro_remaining": macro_remaining,
            "hydration_ml": hydr["ml_total"],
        }, ensure_ascii=False)

    if name == "log_hydration":
        res = await add_hydration(member, inputs["ml"])
        day = _today()
        hydr = await get_hydration_day(member, day)
        return json.dumps({"ok": True, "added_ml": inputs["ml"], "today_ml": hydr["ml_total"]}, ensure_ascii=False)

    if name == "get_hydration":
        day = inputs.get("date") or _today()
        hydr = await get_hydration_day(member, day)
        return json.dumps({"member": member, "date": day, "ml_total": hydr["ml_total"]}, ensure_ascii=False)

    if name == "lookup_nutrition":
        res = await nutrition.lookup_food(inputs["query"])
        return json.dumps(res, ensure_ascii=False) if res else "null"

    if name == "lookup_barcode":
        res = await nutrition.lookup_barcode(inputs["code"])
        return json.dumps(res, ensure_ascii=False) if res else f"Prodotto '{inputs['code']}' non trovato."

    if name == "add_shopping_items":
        n = await add_shopping_items(inputs.get("items", []))
        return json.dumps({"ok": True, "added": n}, ensure_ascii=False)

    if name == "get_shopping_list":
        lst = await get_shopping_list(inputs.get("include_checked", False))
        return json.dumps(lst, ensure_ascii=False) if lst else "Lista spesa vuota."

    if name == "check_shopping_item":
        ok = await check_shopping_item(inputs["name"])
        return json.dumps({"ok": ok, "item": inputs["name"]}, ensure_ascii=False)

    if name == "clear_shopping_list":
        n = await clear_shopping_list(inputs.get("only_checked", False))
        return json.dumps({"ok": True, "removed": n}, ensure_ascii=False)

    if name == "sync_shopping_to_ha":
        lst = await get_shopping_list(False)
        if not lst:
            return "Lista spesa interna vuota: niente da sincronizzare."
        # risolvi lista todo HA
        states = await get_states(None)
        todos = [s["entity_id"] for s in states if s["entity_id"].startswith("todo.")]
        if not todos:
            return "Nessuna lista todo in HA. Aggiungi l'integrazione 'Lista cose da fare'/'Shopping List'."
        want = (inputs.get("list") or "").strip().lower()
        target = todos[0]
        if want:
            target = next((e for e in todos if want in e.lower()), todos[0])
        # dedup: leggi voci già presenti nella lista todo
        existing = set()
        try:
            resp = await call_service("todo", "get_items", {"entity_id": target},
                                      return_response=True)
            sr = (resp or {}).get("service_response", {}) or {}
            items = (sr.get(target, {}) or {}).get("items", [])
            existing = {(i.get("summary") or "").strip().lower() for i in items}
        except Exception:
            pass
        added = 0
        skipped = 0
        for it in lst:
            label = it.get("name", "")
            qty = it.get("qty")
            item = f"{label} ({qty})" if qty else label
            if not item:
                continue
            if item.strip().lower() in existing:
                skipped += 1
                continue
            await call_service("todo", "add_item", {"entity_id": target, "item": item})
            existing.add(item.strip().lower())
            added += 1
        return json.dumps({"ok": True, "list": target, "added": added, "skipped": skipped},
                          ensure_ascii=False)

    if name == "add_pantry_items":
        n = await add_pantry_items(inputs.get("items", []))
        return json.dumps({"ok": True, "added": n}, ensure_ascii=False)

    if name == "get_pantry":
        if inputs.get("expiring"):
            items = await get_pantry_expiring(inputs.get("within_days", 3))
        else:
            items = await get_pantry(inputs.get("category"))
        return json.dumps(items, ensure_ascii=False) if items else "Dispensa vuota."

    if name == "consume_pantry_item":
        n = await consume_pantry_item(inputs["name"])
        return json.dumps({"ok": True, "removed": n}, ensure_ascii=False)

    if name == "clear_pantry":
        n = await clear_pantry()
        return json.dumps({"ok": True, "removed": n}, ensure_ascii=False)

    if name == "save_diet":
        os.makedirs(_DIET_WRITE_DIR, exist_ok=True)
        slug = _slugify(inputs["name"])
        path = os.path.join(_DIET_WRITE_DIR, f"{slug}.md")
        header = f"# {inputs['name'].strip()}\n\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(header + inputs["content"].strip() + "\n")
        return json.dumps({"ok": True, "saved": f"{slug}.md", "dir": _DIET_WRITE_DIR}, ensure_ascii=False)

    if name == "list_diets":
        names = []
        for d in _DIET_DIRS:
            if d and os.path.isdir(d):
                names += [os.path.basename(f) for ext in _DIET_EXTS
                          for f in glob.glob(os.path.join(d, ext))]
        return json.dumps(sorted(set(names)), ensure_ascii=False) if names else "Nessuna dieta caricata."

    return f"Tool sconosciuto nel modulo {NAME}: {name}"
