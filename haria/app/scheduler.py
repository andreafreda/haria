import logging
from datetime import datetime, date, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from memory import (
    get_active_reminders, deactivate_reminder,
    get_meal_plan, get_day_totals, get_profile,
)
import config as cfg

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None
_bot = None


async def _fire(reminder_id: int, user_id: str, message: str, recurring: str | None):
    try:
        await _bot.send_message(chat_id=int(user_id), text=f"⏰ Promemoria: {message}")
        logger.info("Promemoria %s inviato a %s", reminder_id, user_id)
    except Exception as e:
        logger.error("Invio promemoria %s fallito: %s", reminder_id, e)
    if not recurring:
        await deactivate_reminder(reminder_id)


def _schedule_one(r: dict) -> bool:
    rid = r["id"]
    job_id = f"reminder_{rid}"
    if r["recurring"]:
        try:
            trigger = CronTrigger.from_crontab(r["recurring"])
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
        try:
            await _bot.send_message(chat_id=int(chat_id), text=txt)
        except Exception as e:
            logger.warning("Invio report settimanale a %s fallito: %s", chat_id, e)


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
    logger.info("Food jobs proattivi registrati (piano 08:00, report dom 20:00).")
