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
)
import nutrition

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
_DIET_DIRS = [
    os.path.join(os.path.dirname(__file__), "..", "diets"),
    os.environ.get("DIETS_PATH", "/config/haria_diets"),
]
_DIET_EXTS = ("*.md", "*.txt")


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
        "description": "Fissa o sostituisce UN pasto nel piano (es. dopo che l'utente sceglie un'alternativa). Sovrascrive il pasto esistente per quella data+tipo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Data ISO YYYY-MM-DD"},
                "meal_type": {"type": "string", "enum": ["colazione", "pranzo", "cena", "snack"]},
                "items": {"type": "string", "description": "Cosa si mangia"},
                "recipe": {"type": "string"},
                "servings": {"type": "integer"},
                "kcal": {"type": "number", "description": "kcal stimate del pasto a porzione"},
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
    "\n- VALORI NUTRIZIONALI: prima di stimare kcal/macro a memoria, prova lookup_nutrition per dati reali (cache locale)."
    " Per prodotti confezionati col codice a barre usa lookup_barcode. Se la fonte non risponde, stima tu."
    "\n- RIEPILOGO: per 'quanto ho mangiato/quante calorie restano' usa get_daily_summary. Per l'acqua usa log_hydration/get_hydration."
    "\n- SPESA: per generare la lista della spesa, leggi il piano (get_meal_plan), GENERA tu gli ingredienti aggregati e salvali con add_shopping_items."
    " Per consultarla usa get_shopping_list, per spuntare check_shopping_item, per svuotare clear_shopping_list."
    "\n- CONSIGLI: quando proponi cosa cucinare, tieni conto di profili/obiettivi/allergie e privilegia ricette semplici e veloci; offri sempre alternative."
)

PROMPT += diet_prompt()


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


async def handle(name: str, inputs: dict, user_id: str) -> str:
    member = _norm(inputs.get("member"))

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
                m.get("recipe"), m.get("servings"), m.get("kcal"),
            )
        return json.dumps({"ok": True, "count": len(meals)}, ensure_ascii=False)

    if name == "get_meal_plan":
        plan = await get_meal_plan(inputs["date_from"], inputs["date_to"])
        return json.dumps(plan, ensure_ascii=False) if plan else "Nessun piano per le date richieste."

    if name == "set_plan_meal":
        await set_plan_meal(
            inputs["date"], inputs["meal_type"], inputs["items"],
            inputs.get("recipe"), inputs.get("servings"), inputs.get("kcal"),
        )
        return json.dumps({"ok": True, "date": inputs["date"], "meal_type": inputs["meal_type"]}, ensure_ascii=False)

    if name == "get_daily_summary":
        day = inputs.get("date") or _today()
        totals = await get_day_totals(member, day)
        hydr = await get_hydration_day(member, day)
        p = await get_profile(member)
        target = p.get("kcal_target") if p else None
        remaining = round(target - totals["kcal"], 1) if target else None
        return json.dumps({
            "member": member, "date": day, "consumed": totals,
            "kcal_target": target, "kcal_remaining": remaining,
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

    return f"Tool sconosciuto nel modulo {NAME}: {name}"
