import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from memory import get_active_reminders, deactivate_reminder

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
