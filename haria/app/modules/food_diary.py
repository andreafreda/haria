"""Modulo food_diary: profili nutrizionali, peso/BMI, log pasti.

Fase 0 (onboarding profili) + Fase 1 (log pasti). La stima nutrizionale
dei pasti la fornisce Claude stesso negli input di log_meal (grammi, kcal,
macro), così non serve una seconda chiamata API. BMI e fabbisogno calorico
sono calcolati in Python (deterministici).
"""
import json
from memory import (
    get_profile, upsert_profile, add_weight, get_weight_history,
    add_meal, get_meals,
)

NAME = "food_diary"

ACTIVITY_FACTORS = {
    "sedentario": 1.2,
    "leggero": 1.375,
    "moderato": 1.55,
    "attivo": 1.725,
    "molto_attivo": 1.9,
}


def _norm(member: str) -> str:
    return (member or "").strip().lower()


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
]

PROMPT = (
    "\n- DIARIO ALIMENTARE: per registrare pasti usa log_meal e STIMA tu grammi/kcal/macro di ogni alimento (porzioni realistiche)."
    " Per peso usa log_weight, per i profili set_diet_profile/get_diet_profile."
    " Se l'utente non indica il membro, usa il nome dell'utente corrente come 'member'."
    " Un utente può registrare per un altro membro (es. la bimba): in tal caso usa il nome del membro indicato."
    " Per query storiche usa get_meals/get_weight_history."
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

    return f"Tool sconosciuto nel modulo {NAME}: {name}"
