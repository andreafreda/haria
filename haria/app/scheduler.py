"""Scheduler HARIA (APScheduler asyncio): promemoria one-shot/ricorrenti,
briefing news cron, e job proattivi food_diary (piano del giorno, scadenze
dispensa, report settimanale) + refresh sensori MQTT."""
import asyncio
import logging
import re
from datetime import datetime, date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from memory import (
    get_active_reminders, deactivate_reminder,
    get_meal_plan, get_day_totals, get_profile,
    get_pantry_expiring, get_shopping_cost,
    get_active_briefings,
)
import config as cfg

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot = None

# Unix crontab usa dow 0-6 = dom-sab (0/7=dom). APScheduler usa 0-6 = lun-dom.
# from_crontab NON converte -> sfasamento di un giorno. Mappiamo i numeri Unix
# ai nomi APScheduler (non ambigui) e costruiamo il CronTrigger a mano.
_DOW = {"0": "sun", "7": "sun", "1": "mon", "2": "tue", "3": "wed",
        "4": "thu", "5": "fri", "6": "sat"}


def _conv_dow(field: str) -> str:
    """Converte i numeri dow Unix in nomi APScheduler (gestisce *, liste, range)."""
    if field == "*":
        return "*"
    return re.sub(r"\d+", lambda m: _DOW.get(m.group(0), m.group(0)), field)


def _cron_trigger(expr: str) -> CronTrigger:
    """Crea un CronTrigger da un'espressione crontab standard a 5 campi,
    con dow in semantica Unix (5=venerdì). Usa la tz dello scheduler."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron deve avere 5 campi, ricevuto: {expr!r}")
    minute, hour, dom, month, dow = parts
    return CronTrigger(minute=minute, hour=hour, day=dom, month=month,
                       day_of_week=_conv_dow(dow))


async def _fire(reminder_id: int, user_id: str, message: str, recurring: str | None):
    # disattiva il one-shot SOLO a invio riuscito: se Telegram e' irraggiungibile
    # un retry dopo 60s; se fallisce anche quello il reminder resta attivo e
    # verra' ricaricato al prossimo riavvio (no perdita).
    sent = False
    for attempt in range(2):
        try:
            await _bot.send_message(chat_id=int(user_id), text=f"⏰ Promemoria: {message}")
            logger.info("Promemoria %s inviato a %s", reminder_id, user_id)
            sent = True
            break
        except Exception as e:
            logger.error("Invio promemoria %s fallito (tentativo %d): %s",
                         reminder_id, attempt + 1, e)
            if attempt == 0:
                await asyncio.sleep(60)
    if not recurring and sent:
        await deactivate_reminder(reminder_id)


def _schedule_one(r: dict) -> bool:
    rid = r["id"]
    job_id = f"reminder_{rid}"
    if r["recurring"]:
        try:
            trigger = _cron_trigger(r["recurring"])
        except ValueError as e:
            logger.warning("Cron non valido per promemoria %s: %s", rid, e)
            return False
    else:
        when = datetime.fromisoformat(r["remind_at"])
        if when <= datetime.now():
            return False
        trigger = DateTrigger(run_date=when)
    _scheduler.add_job(
        _fire, trigger, id=job_id, replace_existing=True,
        args=[rid, r["user_id"], r["message"], r["recurring"]],
    )
    return True


async def start(bot):
    global _scheduler, _bot
    _bot = bot
    _scheduler = AsyncIOScheduler()
    loaded = 0
    for r in await get_active_reminders():
        if _schedule_one(r):
            loaded += 1
    _scheduler.start()
    logger.info("Scheduler avviato: %d promemoria caricati.", loaded)


def schedule_reminder(r: dict) -> bool:
    if not _scheduler:
        return False
    return _schedule_one(r)


def cancel_job(reminder_id: int):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(f"reminder_{reminder_id}")
    except Exception:
        pass


# ---- briefing news ----

async def _fire_briefing(briefing_id: int, user_id: str, topics: str, num_news: int = 5):
    try:
        from modules import news
        text = await news.generate(topics, user_id, num_news)
        await _bot.send_message(chat_id=int(user_id), text=text)
        logger.info("Briefing %s inviato a %s", briefing_id, user_id)
    except Exception as e:
        logger.error("Briefing %s fallito: %s", briefing_id, e)


def _schedule_briefing_one(b: dict) -> bool:
    try:
        trigger = _cron_trigger(b["cron"])
    except ValueError as e:
        logger.warning("Cron non valido per briefing %s: %s", b["id"], e)
        return False
    _scheduler.add_job(
        _fire_briefing, trigger, id=f"briefing_{b['id']}", replace_existing=True,
        args=[b["id"], b["user_id"], b["topics"], b.get("num_news", 5)],
    )
    return True


def schedule_briefing(b: dict) -> bool:
    if not _scheduler:
        return False
    return _schedule_briefing_one(b)


def cancel_briefing(briefing_id: int):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(f"briefing_{briefing_id}")
    except Exception:
        pass


async def load_briefings():
    """Carica i briefing attivi nello scheduler. Richiede scheduler avviato."""
    if not _scheduler:
        return
    n = 0
    for b in await get_active_briefings():
        if _schedule_briefing_one(b):
            n += 1
    logger.info("Briefing caricati: %d.", n)


def shutdown():
    if _scheduler:
        _scheduler.shutdown(wait=False)


# ---- food_diary: notifiche proattive ----

_MEAL_ORDER = {"colazione": 0, "pranzo": 1, "snack": 2, "cena": 3}


async def _food_morning():
    """Manda a ogni utente il piano pasti di oggi."""
    today = date.today().isoformat()
    plan = await get_meal_plan(today, today)
    if not plan:
        return
    plan.sort(key=lambda m: (_MEAL_ORDER.get(m["meal_type"], 9), m.get("member") or ""))
    lines = ["🍽️ Oggi si mangia:"]
    for m in plan:
        who = (m.get("member") or "").strip()
        prefix = f"{who.capitalize()} — " if who else ""
        line = f"• {m['meal_type'].capitalize()}: {prefix}{m['items']}"
        if m.get("recipe"):
            line += f" — {m['recipe']}"
        if m.get("kcal"):
            line += f" ({round(m['kcal'])} kcal)"
        lines.append(line)
    tot = sum(m.get("kcal") or 0 for m in plan)
    if tot:
        lines.append(f"Totale stimato: {round(tot)} kcal")
    text = "\n".join(lines)
    for u in cfg.get("users", []):
        chat_id = u.get("chat_id")
        if not chat_id:
            continue
        try:
            await _bot.send_message(chat_id=int(chat_id), text=text)
        except Exception as e:
            logger.warning("Invio piano giornaliero a %s fallito: %s", chat_id, e)


async def _food_weekly():
    """Report settimanale: media kcal/giorno per membro."""
    end = date.today()
    start = end - timedelta(days=6)
    cost = await get_shopping_cost(include_checked=True)
    cost_line = ""
    if cost["count"] and cost["priced"]:
        cost_line = f"\n🛒 Spesa attuale: €{cost['total']:.2f} ({cost['priced']}/{cost['count']} voci con prezzo)."
    for u in cfg.get("users", []):
        chat_id = u.get("chat_id")
        member = (u.get("name") or "").strip().lower()
        if not chat_id or not member:
            continue
        days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
        totals = [await get_day_totals(member, d) for d in days]
        active = [t for t in totals if t["meals"] > 0]
        if not active:
            continue
        avg = round(sum(t["kcal"] for t in active) / len(active))
        p = await get_profile(member)
        target = p.get("kcal_target") if p else None
        txt = f"📊 Report settimanale {u.get('name')}:\nMedia {avg} kcal/giorno ({len(active)} giorni tracciati)."
        if target:
            delta = avg - target
            verso = "sopra" if delta > 0 else "sotto"
            txt += f"\nObiettivo {target} kcal → {abs(delta)} kcal {verso} di media."
        txt += cost_line
        try:
            await _bot.send_message(chat_id=int(chat_id), text=txt)
        except Exception as e:
            logger.warning("Invio report settimanale a %s fallito: %s", chat_id, e)


async def _pantry_alert():
    """Avvisa gli utenti se ci sono scorte in scadenza entro 3 giorni."""
    exp = await get_pantry_expiring(3)
    if not exp:
        return
    lines = ["⚠️ In scadenza in dispensa:"]
    for it in exp:
        s = f"• {it['name']}"
        if it.get("qty"):
            s += f" {it['qty']}"
        if it.get("expires_on"):
            s += f" — scad. {it['expires_on']}"
        lines.append(s)
    text = "\n".join(lines)
    for u in cfg.get("users", []):
        chat_id = u.get("chat_id")
        if not chat_id:
            continue
        try:
            await _bot.send_message(chat_id=int(chat_id), text=text)
        except Exception as e:
            logger.warning("Invio alert dispensa a %s fallito: %s", chat_id, e)


async def _mqtt_refresh():
    try:
        import mqtt_pub
        await mqtt_pub.refresh()
    except Exception as e:
        logger.debug("Refresh MQTT fallito: %s", e)


def schedule_mqtt_refresh():
    """Aggiorna i sensori MQTT cibo ogni 5 minuti."""
    if not _scheduler:
        return
    from apscheduler.triggers.interval import IntervalTrigger
    _scheduler.add_job(_mqtt_refresh, IntervalTrigger(minutes=5),
                       id="mqtt_refresh", replace_existing=True)
    logger.info("Refresh MQTT cibo registrato (ogni 5 min).")


def is_running() -> bool:
    """True se lo scheduler è stato avviato."""
    return _scheduler is not None


def schedule_econ_refresh():
    """Ripubblica i sensori economia+bollette ogni notte alle 00:05, così a
    cavallo del mese spese_mese/budget/confronto non restano sul mese vecchio."""
    if not _scheduler:
        return

    async def _job():
        try:
            import mqtt_pub
            await mqtt_pub.publish_economia()
            await mqtt_pub.publish_bollette()
        except Exception as e:
            logger.debug("Refresh economia/bollette fallito: %s", e)

    _scheduler.add_job(_job, CronTrigger(hour=0, minute=5),
                       id="econ_refresh", replace_existing=True)
    logger.info("Refresh notturno economia/bollette registrato (00:05).")


def schedule_food_jobs(bot):
    """Registra job proattivi food_diary. Richiede scheduler già avviato."""
    global _bot
    _bot = bot
    if not _scheduler:
        logger.warning("Scheduler non avviato: food jobs non registrati.")
        return
    _scheduler.add_job(_food_morning, CronTrigger(hour=8, minute=0),
                       id="food_morning", replace_existing=True)
    _scheduler.add_job(_food_weekly, CronTrigger(day_of_week="sun", hour=20, minute=0),
                       id="food_weekly", replace_existing=True)
    _scheduler.add_job(_pantry_alert, CronTrigger(hour=8, minute=30),
                       id="pantry_alert", replace_existing=True)
    logger.info("Food jobs proattivi registrati (piano 08:00, scadenze 08:30, report dom 20:00).")
