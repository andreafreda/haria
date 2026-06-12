"""Publisher MQTT: espone i dati food_diary come entità Home Assistant.

Usa il broker MQTT fornito dal Supervisor (servizio `mqtt:want`). Pubblica
MQTT Discovery (retain) così HA crea automaticamente i sensori, poi aggiorna
gli stati. Tutto best-effort: se MQTT non c'è, ogni funzione è no-op.

Entità create (device "HARIA Cibo"):
  per membro: kcal_oggi, kcal_target, proteine_oggi/target, carbo_oggi/target,
              grassi_oggi/target, acqua_oggi
  globali:    piano_oggi (testo+attributi), piano_settimana (attributi giorni),
              spesa (conteggio)
"""
import asyncio
import contextvars
import json
import logging
import os
from datetime import date, timedelta

import aiohttp

import config as cfg
from memory import (
    list_profiles, get_day_totals, get_hydration_day,
    get_meal_plan, get_meals, get_shopping_list, get_shopping_cost, get_profile,
    get_pantry, get_pantry_expiring, get_weight_stats,
    get_bolletta_csv, get_bolletta_years,
    get_saldi, get_budget_status, riepilogo_spese, get_obiettivi,
    get_mqtt_topics, set_mqtt_topics,
)

# Collector dei topic pubblicati nel giro corrente (per il cleanup delle entità
# fantasma). ContextVar => async-safe tra publish concorrenti (ogni task ha la
# propria copia). None = nessuna raccolta attiva.
_collect_var: contextvars.ContextVar = contextvars.ContextVar("mqtt_collect", default=None)

logger = logging.getLogger(__name__)

_client = None
_enabled = False
_DISC = "homeassistant"        # prefisso discovery HA
_BASE = "haria/food"           # prefisso state topic
_DEVICE = {
    "identifiers": ["haria_cibo"],
    "name": "HARIA Cibo",
    "manufacturer": "HARIA",
    "model": "food_diary",
}
_MEAL_ORDER = {"colazione": 0, "pranzo": 1, "snack": 2, "cena": 3}

# --- Bollette (device separato) ---
from bollette_def import UTILITIES as _BOLL_UTILITIES, metrics as _boll_metrics

_BASE_BOLL = "haria/bollette"          # prefisso state topic bollette
_DEVICE_BOLL = {
    "identifiers": ["haria_bollette"],
    "name": "HARIA Bollette",
    "manufacturer": "HARIA",
    "model": "bollette",
}

# --- Economia (device separato) ---
from econ_def import CONTI as _ECON_CONTI

_BASE_ECON = "haria/economia"          # prefisso state topic economia
_DEVICE_ECON = {
    "identifiers": ["haria_economia"],
    "name": "HARIA Economia",
    "manufacturer": "HARIA",
    "model": "economia",
}


def _slug(s: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_]+", "_", (s or "").strip().lower()).strip("_")


def _fmt_plan_item(m: dict) -> str:
    """Formatta voce piano: prefisso nome se override personale, kcal se nota."""
    who = (m.get("member") or "").strip()
    s = f"{who.capitalize()}: {m['items']}" if who else m["items"]
    if m.get("kcal"):
        s += f" ({round(m['kcal'])} kcal)"
    return s


async def _mqtt_config() -> dict | None:
    """Recupera credenziali broker dal Supervisor."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                "http://supervisor/services/mqtt",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    logger.warning("Servizio MQTT non disponibile (status %s)", r.status)
                    return None
                data = (await r.json()).get("data", {})
        return data
    except Exception as e:
        logger.warning("Config MQTT non recuperabile: %s", e)
        return None


async def start():
    """Connette al broker e pubblica discovery+stato iniziale."""
    global _client, _enabled
    conf = await _mqtt_config()
    if not conf:
        logger.info("MQTT non configurato: dashboard cibo HA disattivata.")
        return
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        logger.warning("paho-mqtt non installato: dashboard cibo HA disattivata.")
        return
    try:
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="haria_food")
        if conf.get("username"):
            c.username_pw_set(conf.get("username"), conf.get("password"))
        if conf.get("ssl"):
            c.tls_set()
        # connect e' I/O sincrono: in thread per non bloccare l'event loop
        # (broker lento/irraggiungibile renderebbe HARIA muto su Telegram)
        await asyncio.to_thread(c.connect, conf["host"], int(conf.get("port", 1883)), 60)
        c.loop_start()
        _client = c
        _enabled = True
        logger.info("MQTT connesso (%s:%s).", conf["host"], conf.get("port"))
    except Exception as e:
        logger.warning("Connessione MQTT fallita: %s", e)
        return
    try:
        await publish_discovery()
        await refresh()
        await publish_bollette()
        await publish_economia()
    except Exception as e:
        logger.warning("Pubblicazione iniziale MQTT fallita: %s", e)


def stop():
    global _client, _enabled
    if _client:
        try:
            _client.loop_stop()
            _client.disconnect()
        except Exception:
            pass
    _client = None
    _enabled = False


def _pub(topic: str, payload, retain: bool = True):
    if not _enabled or not _client:
        return
    if not isinstance(payload, str):
        payload = json.dumps(payload, ensure_ascii=False)
    try:
        _client.publish(topic, payload, retain=retain)
    except Exception as e:
        logger.debug("Publish MQTT fallito su %s: %s", topic, e)


def _disc_sensor(uid: str, name: str, state_topic: str, unit: str | None = None,
                 icon: str | None = None, value_template: str | None = None,
                 json_attr_topic: str | None = None, state_class: str | None = None,
                 device_class: str | None = None, device: dict | None = None,
                 object_id: str | None = None):
    cfg_topic = f"{_DISC}/sensor/{uid}/config"
    payload = {
        "name": name,
        "unique_id": uid,
        "state_topic": state_topic,
        "device": device or _DEVICE,
    }
    if object_id:
        payload["object_id"] = object_id
    if unit:
        payload["unit_of_measurement"] = unit
    if icon:
        payload["icon"] = icon
    if value_template:
        payload["value_template"] = value_template
    if json_attr_topic:
        payload["json_attributes_topic"] = json_attr_topic
    if state_class:
        payload["state_class"] = state_class
    if device_class:
        payload["device_class"] = device_class
    coll = _collect_var.get()
    if coll is not None:
        st = [state_topic]
        if json_attr_topic:
            st.append(json_attr_topic)
        coll.append({"uid": uid, "config_topic": cfg_topic, "state_topics": st})
    _pub(cfg_topic, payload)


async def _cleanup_stale(kind: str, collected: list):
    """Rimuove dalle entità HA quelle non più pubblicate in questo giro: payload
    vuoto sul config topic = HA elimina l'entità; svuota anche i retained state."""
    cur_uids = {r["uid"] for r in collected}
    for old in await get_mqtt_topics(kind):
        if old["uid"] not in cur_uids:
            _pub(old["config_topic"], "", retain=True)
            for st in old.get("state_topics", []):
                _pub(st, "", retain=True)
    await set_mqtt_topics(kind, collected)


async def _members() -> list[str]:
    profs = await list_profiles()
    return [p["member"] for p in profs if p.get("member")]


async def publish_discovery():
    """Crea/aggiorna le entità via MQTT Discovery."""
    token = _collect_var.set([])
    try:
        await _publish_discovery_body()
    finally:
        collected = _collect_var.get()
        _collect_var.reset(token)
    await _cleanup_stale("food", collected)


async def _publish_discovery_body():
    for m in await _members():
        s = _slug(m)
        base = f"{_BASE}/{s}"
        cap = m.capitalize()
        _disc_sensor(f"haria_{s}_kcal_oggi", f"{cap} kcal oggi", f"{base}/kcal_oggi", "kcal", "mdi:fire")
        _disc_sensor(f"haria_{s}_kcal_target", f"{cap} kcal target", f"{base}/kcal_target", "kcal", "mdi:target")
        _disc_sensor(f"haria_{s}_proteine_oggi", f"{cap} proteine oggi", f"{base}/proteine_oggi", "g", "mdi:food-drumstick")
        _disc_sensor(f"haria_{s}_proteine_target", f"{cap} proteine target", f"{base}/proteine_target", "g", "mdi:food-drumstick-outline")
        _disc_sensor(f"haria_{s}_carbo_oggi", f"{cap} carboidrati oggi", f"{base}/carbo_oggi", "g", "mdi:bread-slice")
        _disc_sensor(f"haria_{s}_carbo_target", f"{cap} carboidrati target", f"{base}/carbo_target", "g", "mdi:bread-slice-outline")
        _disc_sensor(f"haria_{s}_grassi_oggi", f"{cap} grassi oggi", f"{base}/grassi_oggi", "g", "mdi:oil")
        _disc_sensor(f"haria_{s}_grassi_target", f"{cap} grassi target", f"{base}/grassi_target", "g", "mdi:oil")
        _disc_sensor(f"haria_{s}_acqua_oggi", f"{cap} acqua oggi", f"{base}/acqua_oggi", "mL", "mdi:cup-water")
        _disc_sensor(f"haria_{s}_peso", f"{cap} peso", f"{base}/peso", "kg", "mdi:scale-bathroom",
                     state_class="measurement", device_class="weight")
        _disc_sensor(f"haria_{s}_peso_delta_30d", f"{cap} peso Δ 30g", f"{base}/peso_delta_30d", "kg", "mdi:scale-balance")
        _disc_sensor(f"haria_{s}_peso_min_30d", f"{cap} peso min 30g", f"{base}/peso_min_30d", "kg", "mdi:arrow-down-bold")
        _disc_sensor(f"haria_{s}_peso_max_30d", f"{cap} peso max 30g", f"{base}/peso_max_30d", "kg", "mdi:arrow-up-bold")
        _disc_sensor(f"haria_{s}_bmi", f"{cap} BMI", f"{base}/bmi", icon="mdi:human",
                     state_class="measurement")
        _disc_sensor(f"haria_{s}_diario_settimana", f"{cap} diario settimana", f"{base}/diario_settimana/state",
                     icon="mdi:book-open-variant", json_attr_topic=f"{base}/diario_settimana/attr")
    # globali
    _disc_sensor("haria_piano_oggi", "Piano oggi", f"{_BASE}/piano_oggi/state",
                 icon="mdi:silverware-fork-knife", json_attr_topic=f"{_BASE}/piano_oggi/attr")
    _disc_sensor("haria_piano_settimana", "Piano settimana", f"{_BASE}/piano_settimana/state",
                 icon="mdi:calendar-week", json_attr_topic=f"{_BASE}/piano_settimana/attr")
    _disc_sensor("haria_piano_mese", "Piano mese", f"{_BASE}/piano_mese/state",
                 icon="mdi:calendar-month", json_attr_topic=f"{_BASE}/piano_mese/attr")
    _disc_sensor("haria_spesa", "Lista spesa", f"{_BASE}/spesa/state", "voci",
                 icon="mdi:cart", json_attr_topic=f"{_BASE}/spesa/attr")
    _disc_sensor("haria_spesa_costo", "Spesa costo", f"{_BASE}/spesa_costo/state", "€",
                 icon="mdi:currency-eur", json_attr_topic=f"{_BASE}/spesa_costo/attr")
    _disc_sensor("haria_dispensa", "Dispensa", f"{_BASE}/dispensa/state", "voci",
                 icon="mdi:fridge", json_attr_topic=f"{_BASE}/dispensa/attr")
    _disc_sensor("haria_dispensa_scadenze", "Dispensa in scadenza", f"{_BASE}/dispensa_scadenze/state",
                 "voci", icon="mdi:clock-alert", json_attr_topic=f"{_BASE}/dispensa_scadenze/attr")


async def refresh():
    """Aggiorna gli stati di tutte le entità."""
    if not _enabled:
        return
    from modules.food_diary import compute_macro_targets  # evita import circolare
    today = date.today().isoformat()

    for m in await _members():
        s = _slug(m)
        base = f"{_BASE}/{s}"
        tot = await get_day_totals(m, today)
        hydr = await get_hydration_day(m, today)
        p = await get_profile(m)
        kcal_t = p.get("kcal_target") if p else None
        macros = compute_macro_targets(kcal_t, p.get("weight_kg")) if p else None
        _pub(f"{base}/kcal_oggi", round(tot["kcal"]))
        _pub(f"{base}/kcal_target", kcal_t if kcal_t is not None else "")
        _pub(f"{base}/proteine_oggi", round(tot["protein_g"]))
        _pub(f"{base}/carbo_oggi", round(tot["carbs_g"]))
        _pub(f"{base}/grassi_oggi", round(tot["fat_g"]))
        _pub(f"{base}/acqua_oggi", round(hydr["ml_total"]))
        _pub(f"{base}/proteine_target", macros["protein_target_g"] if macros else "")
        _pub(f"{base}/carbo_target", macros["carbs_target_g"] if macros else "")
        _pub(f"{base}/grassi_target", macros["fat_target_g"] if macros else "")
        # peso/bmi da ultimo log reale (fallback su snapshot profilo)
        ws = await get_weight_stats(m, 30)
        if ws:
            weight = ws["latest"]
            bmi = ws["latest_bmi"] if ws["latest_bmi"] is not None else (p.get("bmi") if p else None)
            _pub(f"{base}/peso_delta_30d", ws["delta"])
            _pub(f"{base}/peso_min_30d", ws["min"])
            _pub(f"{base}/peso_max_30d", ws["max"])
        else:
            weight = p.get("weight_kg") if p else None
            bmi = p.get("bmi") if p else None
            _pub(f"{base}/peso_delta_30d", "")
            _pub(f"{base}/peso_min_30d", "")
            _pub(f"{base}/peso_max_30d", "")
        _pub(f"{base}/peso", weight if weight is not None else "")
        _pub(f"{base}/bmi", bmi if bmi is not None else "")
        # diario settimana: pasti registrati realmente (lun-dom corrente)
        d_start = date.today() - timedelta(days=date.today().weekday())
        d_days = [(d_start + timedelta(days=i)) for i in range(7)]
        wmeals = await get_meals(m, d_days[0].isoformat(), d_days[-1].isoformat())
        by_day_d = {}
        for x in wmeals:
            by_day_d.setdefault((x.get("eaten_at") or "")[:10], []).append(x)
        dattr = {}
        for d in d_days:
            iso = d.isoformat()
            ms = sorted(by_day_d.get(iso, []), key=lambda x: _MEAL_ORDER.get(x["meal_type"], 9))
            dattr[iso] = "; ".join(
                f"{x['meal_type']}: {x['description']}"
                + (f" ({round(x['kcal_total'])} kcal)" if x.get("kcal_total") else "")
                for x in ms
            ) if ms else ""
        _pub(f"{base}/diario_settimana/state", f"{len(wmeals)} pasti" if wmeals else "nessun pasto")
        _pub(f"{base}/diario_settimana/attr", dattr)

    # piano oggi (comune + override personali)
    plan = await get_meal_plan(today, today)
    plan.sort(key=lambda x: (_MEAL_ORDER.get(x["meal_type"], 9), x.get("member") or ""))
    attr = {}
    for mt in ("colazione", "pranzo", "snack", "cena"):
        grp = [m for m in plan if m["meal_type"] == mt]
        if not grp:
            continue
        attr[mt] = "; ".join(_fmt_plan_item(m) for m in grp)
    _pub(f"{_BASE}/piano_oggi/state", f"{len(plan)} pasti" if plan else "nessun piano")
    _pub(f"{_BASE}/piano_oggi/attr", attr)

    # piano settimana (lun-dom corrente)
    start_w = date.today() - timedelta(days=date.today().weekday())
    days = [(start_w + timedelta(days=i)) for i in range(7)]
    wplan = await get_meal_plan(days[0].isoformat(), days[-1].isoformat())
    by_day = {}
    for mm in wplan:
        by_day.setdefault(mm["date"], []).append(mm)
    giorni = {}
    for d in days:
        iso = d.isoformat()
        ms = sorted(by_day.get(iso, []),
                    key=lambda x: (_MEAL_ORDER.get(x["meal_type"], 9), x.get("member") or ""))
        giorni[iso] = "; ".join(
            f"{x['meal_type']}: {_fmt_plan_item(x)}" for x in ms
        ) if ms else ""
    _pub(f"{_BASE}/piano_settimana/state", f"{len(wplan)} pasti")
    _pub(f"{_BASE}/piano_settimana/attr", giorni)

    # piano mese (1 -> ultimo giorno mese corrente)
    today_d = date.today()
    first = today_d.replace(day=1)
    if first.month == 12:
        nxt = first.replace(year=first.year + 1, month=1)
    else:
        nxt = first.replace(month=first.month + 1)
    last = nxt - timedelta(days=1)
    mdays = [(first + timedelta(days=i)) for i in range((last - first).days + 1)]
    mplan = await get_meal_plan(first.isoformat(), last.isoformat())
    by_day_m = {}
    for mm in mplan:
        by_day_m.setdefault(mm["date"], []).append(mm)
    mesi = {}
    for d in mdays:
        iso = d.isoformat()
        ms = sorted(by_day_m.get(iso, []),
                    key=lambda x: (_MEAL_ORDER.get(x["meal_type"], 9), x.get("member") or ""))
        mesi[iso] = "; ".join(
            f"{x['meal_type']}: {_fmt_plan_item(x)}" for x in ms
        ) if ms else ""
    _pub(f"{_BASE}/piano_mese/state", f"{len(mplan)} pasti")
    _pub(f"{_BASE}/piano_mese/attr", mesi)

    # spesa
    items = await get_shopping_list(include_checked=False)
    def _fmt_spesa(it):
        s = f"{it['name']} {it.get('qty') or ''}".strip()
        if it.get("price") is not None:
            s += f" — €{it['price']:.2f}"
        return s
    _pub(f"{_BASE}/spesa/state", len(items))
    _pub(f"{_BASE}/spesa/attr", {"voci": [_fmt_spesa(it) for it in items]})

    # spesa costo (totale stimato, intera lista)
    cost = await get_shopping_cost(include_checked=True)
    _pub(f"{_BASE}/spesa_costo/state", cost["total"])
    _pub(f"{_BASE}/spesa_costo/attr", {
        "voci_con_prezzo": cost["priced"], "voci_senza_prezzo": cost["missing"],
        "voci_totali": cost["count"],
    })

    # dispensa
    pantry = await get_pantry()
    def _fmt_pantry(it):
        s = f"{it['name']} {it.get('qty') or ''}".strip()
        if it.get("expires_on"):
            s += f" (scad. {it['expires_on']})"
        return s
    _pub(f"{_BASE}/dispensa/state", len(pantry))
    _pub(f"{_BASE}/dispensa/attr", {"voci": [_fmt_pantry(it) for it in pantry]})
    exp = await get_pantry_expiring(3)
    _pub(f"{_BASE}/dispensa_scadenze/state", len(exp))
    _pub(f"{_BASE}/dispensa_scadenze/attr", {"voci": [_fmt_pantry(it) for it in exp]})


async def publish_bollette():
    """Discovery + stato delle serie bollette (device 'HARIA Bollette').

    Per ogni utility/metric pubblica un sensore per anno, stato = CSV 12 mesi.
    Le serie sono guidate da bollette_def.UTILITIES → estendibile aggiungendo
    una utenza lì (es. telefono), senza toccare questo file."""
    if not _enabled:
        return
    token = _collect_var.set([])
    try:
        await _publish_bollette_body()
    finally:
        collected = _collect_var.get()
        _collect_var.reset(token)
    await _cleanup_stale("bollette", collected)


async def _publish_bollette_body():
    cur_year = date.today().year
    for util, d in _BOLL_UTILITIES.items():
        label = d["label"]
        for metric, icon, unit_label in _boll_metrics(util):
            # range continuo (corrente-2 … corrente) + eventuali anni storici:
            # garantisce che i sensori-anno mostrati dalla dashboard esistano
            # sempre (zeri se senza dati), evitando "entity not available".
            years = set(await get_bolletta_years(util, metric)) | {
                cur_year, cur_year - 1, cur_year - 2}
            for year in sorted(years):
                uid = f"haria_boll_{util}_{metric}_{year}"
                topic = f"{_BASE_BOLL}/{util}/{metric}/{year}"
                _disc_sensor(
                    uid, f"{label} {unit_label} {year}", topic,
                    icon=icon, device=_DEVICE_BOLL,
                    object_id=f"bollette_{util}_{metric}_{year}",
                )
                _pub(topic, await get_bolletta_csv(util, metric, year))


def _month_bounds():
    """(primo_giorno, ultimo_giorno, anno, mese) del mese corrente, ISO."""
    today = date.today()
    first = today.replace(day=1)
    nxt = first.replace(year=first.year + 1, month=1) if first.month == 12 \
        else first.replace(month=first.month + 1)
    last = nxt - timedelta(days=1)
    return first.isoformat(), last.isoformat(), today.year, today.month


def _prev_month_bounds():
    """(primo_giorno, ultimo_giorno) del mese precedente, ISO."""
    first = date.today().replace(day=1)
    p_last = first - timedelta(days=1)
    p_first = p_last.replace(day=1)
    return p_first.isoformat(), p_last.isoformat()


async def publish_economia():
    """Discovery + stato economia (device 'HARIA Economia').

    Sensori: saldo per conto + saldo totale; spese del mese (con breakdown
    per categoria); stato budget del mese per categoria con tetto impostato."""
    if not _enabled:
        return
    token = _collect_var.set([])
    try:
        await _publish_economia_body()
    finally:
        collected = _collect_var.get()
        _collect_var.reset(token)
    await _cleanup_stale("economia", collected)


async def _publish_economia_body():
    # --- saldi conti ---
    saldi = await get_saldi()
    totale = 0.0
    per_intest: dict = {}
    for s in saldi:
        slug = _slug(s["conto"])
        label = _ECON_CONTI.get(s["conto"], {}).get("label", s["conto"].capitalize())
        icon = _ECON_CONTI.get(s["conto"], {}).get("icon", "mdi:wallet")
        topic = f"{_BASE_ECON}/saldo/{slug}"
        _disc_sensor(
            f"haria_econ_saldo_{slug}", f"Saldo {label}", topic, "€",
            icon=icon, device=_DEVICE_ECON, state_class="measurement",
            object_id=f"economia_saldo_{slug}",
        )
        _pub(topic, s["saldo"])
        totale += s["saldo"]
        intest = s.get("intestatario", "famiglia")
        per_intest[intest] = round(per_intest.get(intest, 0.0) + s["saldo"], 2)
    _disc_sensor(
        "haria_econ_saldo_totale", "Saldo totale", f"{_BASE_ECON}/saldo_totale", "€",
        icon="mdi:cash-multiple", device=_DEVICE_ECON, state_class="measurement",
        object_id="economia_saldo_totale",
    )
    _pub(f"{_BASE_ECON}/saldo_totale", round(totale, 2))
    # saldo aggregato per intestatario (andrea/marina/famiglia)
    for intest, val in per_intest.items():
        slug = _slug(intest)
        topic = f"{_BASE_ECON}/saldo_intestatario/{slug}"
        _disc_sensor(
            f"haria_econ_saldo_int_{slug}", f"Saldo {intest.capitalize()}", topic, "€",
            icon="mdi:account-cash", device=_DEVICE_ECON, state_class="measurement",
            object_id=f"economia_saldo_intestatario_{slug}",
        )
        _pub(topic, val)

    # --- spese del mese (totale uscite + breakdown per categoria) ---
    first, last, year, month = _month_bounds()
    rep = await riepilogo_spese(data_da=first, data_a=last)
    _disc_sensor(
        "haria_econ_spese_mese", "Spese mese", f"{_BASE_ECON}/spese_mese/state", "€",
        icon="mdi:cart-arrow-down", device=_DEVICE_ECON,
        json_attr_topic=f"{_BASE_ECON}/spese_mese/attr",
        object_id="economia_spese_mese",
    )
    # uscite e' negativo -> mostra valore assoluto speso
    spese_mese = round(-rep["uscite"], 2)
    _pub(f"{_BASE_ECON}/spese_mese/state", spese_mese)
    _pub(f"{_BASE_ECON}/spese_mese/attr", {
        "entrate": rep["entrate"], "uscite": rep["uscite"], "netto": rep["netto"],
        "per_categoria": {c["categoria"]: c["totale"] for c in rep["per_categoria"]},
    })

    # --- confronto col mese precedente ---
    p_first, p_last = _prev_month_bounds()
    rep_prev = await riepilogo_spese(data_da=p_first, data_a=p_last)
    spese_prec = round(-rep_prev["uscite"], 2)
    delta = round(spese_mese - spese_prec, 2)
    _disc_sensor(
        "haria_econ_spese_mese_prec", "Spese mese scorso",
        f"{_BASE_ECON}/spese_mese_prec", "€",
        icon="mdi:cart-outline", device=_DEVICE_ECON,
        object_id="economia_spese_mese_prec",
    )
    _pub(f"{_BASE_ECON}/spese_mese_prec", spese_prec)
    _disc_sensor(
        "haria_econ_spese_mese_delta", "Spese vs mese scorso",
        f"{_BASE_ECON}/spese_mese_delta", "€",
        icon="mdi:swap-vertical", device=_DEVICE_ECON,
        object_id="economia_spese_mese_delta",
    )
    _pub(f"{_BASE_ECON}/spese_mese_delta", delta)

    # --- budget del mese per categoria ---
    for b in await get_budget_status(year, month):
        slug = _slug(b["categoria"])
        topic = f"{_BASE_ECON}/budget/{slug}"
        _disc_sensor(
            f"haria_econ_budget_{slug}", f"Budget {b['categoria']}",
            f"{topic}/state", "%",
            icon="mdi:gauge", device=_DEVICE_ECON,
            json_attr_topic=f"{topic}/attr",
            object_id=f"economia_budget_{slug}",
        )
        _pub(f"{topic}/state", b["perc"])
        _pub(f"{topic}/attr", {
            "budget": b["budget"], "speso": b["speso"],
            "residuo": b["residuo"], "sforato": b["sforato"],
        })

    # --- salvadanai / obiettivi di risparmio (progress) ---
    for o in await get_obiettivi():
        slug = _slug(o["nome"])
        topic = f"{_BASE_ECON}/obiettivo/{slug}"
        _disc_sensor(
            f"haria_econ_obiettivo_{slug}", f"Obiettivo {o['nome']}",
            f"{topic}/state", "%",
            icon="mdi:piggy-bank", device=_DEVICE_ECON,
            json_attr_topic=f"{topic}/attr",
            object_id=f"economia_obiettivo_{slug}",
        )
        _pub(f"{topic}/state", o["perc"])
        _pub(f"{topic}/attr", {
            "target": o["target"], "accantonato": o["accantonato"],
            "residuo": o["residuo"], "scadenza": o["target_date"] or "",
            "mesi_rimanenti": o["mesi_rimanenti"] if o["mesi_rimanenti"] is not None else "",
            "quota_mensile": o["quota_mensile"] if o["quota_mensile"] is not None else "",
            "raggiunto": o["raggiunto"],
        })


_bg_tasks: set = set()


def _spawn(coro):
    """Lancia una coroutine in background tenendo il riferimento al task
    (altrimenti il GC puo' cancellarlo a meta' — pitfall asyncio)."""
    if not _enabled:
        coro.close()
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("spawn: nessun event loop attivo, skip")
        coro.close()
        return
    t = loop.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


def request_economia_refresh():
    """Trigger non bloccante di publish_economia (dopo mutazioni economia)."""
    _spawn(publish_economia())


def request_refresh():
    """Trigger non bloccante di un refresh (da chiamare dopo mutazioni)."""
    _spawn(refresh())


def request_bollette_refresh():
    """Trigger non bloccante di publish_bollette (dopo update_bill)."""
    _spawn(publish_bollette())
