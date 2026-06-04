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
)

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
        c.connect(conf["host"], int(conf.get("port", 1883)), keepalive=60)
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
                 device_class: str | None = None):
    cfg_topic = f"{_DISC}/sensor/{uid}/config"
    payload = {
        "name": name,
        "unique_id": uid,
        "state_topic": state_topic,
        "device": _DEVICE,
    }
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
    _pub(cfg_topic, payload)


async def _members() -> list[str]:
    profs = await list_profiles()
    return [p["member"] for p in profs if p.get("member")]


async def publish_discovery():
    """Crea/aggiorna le entità via MQTT Discovery."""
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


def request_refresh():
    """Trigger non bloccante di un refresh (da chiamare dopo mutazioni)."""
    if not _enabled:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("request_refresh: nessun event loop attivo, skip")
        return
    loop.create_task(refresh())
