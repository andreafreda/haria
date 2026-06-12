"""HARIA memory — promemoria, briefing, news blocklist, error log
(split da memory.py; API invariata via facade memory/__init__.py)."""
import sys
from . import core
import aiosqlite
import json
from datetime import date, timedelta


def __getattr__(name):
    _c = sys.modules["memory.core"]
    if hasattr(_c, name):
        return getattr(_c, name)
    return getattr(sys.modules["memory"], name)


def _reminder_row(r) -> dict:
    return {
        "id": r[0],
        "user_id": r[1],
        "message": r[2],
        "remind_at": r[3],
        "recurring": r[4],
    }


async def add_reminder(user_id: str, message: str, remind_at: str | None, recurring: str | None) -> dict:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, message, remind_at, recurring) VALUES (?, ?, ?, ?)",
            (user_id, message, remind_at, recurring),
        )
        await db.commit()
        rid = cursor.lastrowid
    return {"id": rid, "user_id": user_id, "message": message, "remind_at": remind_at, "recurring": recurring}


async def get_active_reminders() -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, message, remind_at, recurring FROM reminders WHERE active = 1"
        )
        rows = await cursor.fetchall()
    return [_reminder_row(r) for r in rows]


async def get_user_reminders(user_id: str) -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, message, remind_at, recurring FROM reminders WHERE active = 1 AND user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [_reminder_row(r) for r in rows]


async def deactivate_reminder(reminder_id: int, user_id: str | None = None) -> bool:
    async with aiosqlite.connect(core.DB_PATH) as db:
        if user_id is not None:
            cursor = await db.execute(
                "UPDATE reminders SET active = 0 WHERE id = ? AND user_id = ?",
                (reminder_id, user_id),
            )
        else:
            cursor = await db.execute(
                "UPDATE reminders SET active = 0 WHERE id = ?", (reminder_id,)
            )
        await db.commit()
        return cursor.rowcount > 0


async def update_reminder(reminder_id: int, user_id: str | None = None,
                          message: str | None = None, remind_at: str | None = None,
                          recurring: str | None = None) -> dict | None:
    """Modifica un promemoria attivo. Solo i campi passati vengono aggiornati.
    Per cancellare recurring passa stringa vuota. Ritorna la riga aggiornata o None."""
    sets: list[str] = []
    params: list = []
    if message is not None:
        sets.append("message = ?"); params.append(message)
    if remind_at is not None:
        sets.append("remind_at = ?"); params.append(remind_at)
    if recurring is not None:
        sets.append("recurring = ?"); params.append(recurring or None)
    if not sets:
        return None
    where = "id = ? AND active = 1"
    params2 = list(params) + [int(reminder_id)]
    if user_id is not None:
        where += " AND user_id = ?"
        params2.append(user_id)
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE reminders SET {', '.join(sets)} WHERE {where}", params2
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row = await (await db.execute(
            "SELECT id, user_id, message, remind_at, recurring FROM reminders WHERE id = ?",
            (int(reminder_id),),
        )).fetchone()
    return _reminder_row(row) if row else None


# ---- briefing news ----

def _briefing_row(r) -> dict:
    return {"id": r[0], "user_id": r[1], "topics": r[2], "cron": r[3],
            "num_news": r[4] if len(r) > 4 and r[4] is not None else 5}


async def add_briefing(user_id: str, topics: str, cron: str, num_news: int = 5) -> dict:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO briefings (user_id, topics, cron, num_news) VALUES (?, ?, ?, ?)",
            (user_id, topics, cron, int(num_news)),
        )
        await db.commit()
        bid = cursor.lastrowid
    return {"id": bid, "user_id": user_id, "topics": topics, "cron": cron, "num_news": int(num_news)}


async def get_active_briefings() -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, topics, cron, num_news FROM briefings WHERE active = 1"
        )
        rows = await cursor.fetchall()
    return [_briefing_row(r) for r in rows]


async def get_user_briefings(user_id: str) -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, topics, cron, num_news FROM briefings WHERE active = 1 AND user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [_briefing_row(r) for r in rows]


async def get_briefing(briefing_id: int) -> dict | None:
    async with aiosqlite.connect(core.DB_PATH) as db:
        row = await (await db.execute(
            "SELECT id, user_id, topics, cron, num_news FROM briefings WHERE id = ? AND active = 1",
            (int(briefing_id),),
        )).fetchone()
    return _briefing_row(row) if row else None


async def deactivate_briefing(briefing_id: int, user_id: str | None = None) -> bool:
    where = "id = ?"
    params: list = [int(briefing_id)]
    if user_id is not None:
        where += " AND user_id = ?"
        params.append(user_id)
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(f"UPDATE briefings SET active = 0 WHERE {where}", params)
        await db.commit()
        return cursor.rowcount > 0


async def update_briefing(briefing_id: int, user_id: str | None = None,
                          topics: str | None = None, cron: str | None = None,
                          num_news: int | None = None,
                          new_user_id: str | None = None) -> dict | None:
    sets: list[str] = []
    params: list = []
    if topics is not None:
        sets.append("topics = ?"); params.append(topics)
    if cron is not None:
        sets.append("cron = ?"); params.append(cron)
    if num_news is not None:
        sets.append("num_news = ?"); params.append(int(num_news))
    if new_user_id is not None:
        sets.append("user_id = ?"); params.append(new_user_id)
    if not sets:
        return None
    where = "id = ? AND active = 1"
    params2 = list(params) + [int(briefing_id)]
    if user_id is not None:
        where += " AND user_id = ?"
        params2.append(user_id)
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE briefings SET {', '.join(sets)} WHERE {where}", params2
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
        row = await (await db.execute(
            "SELECT id, user_id, topics, cron, num_news FROM briefings WHERE id = ?",
            (int(briefing_id),),
        )).fetchone()
    return _briefing_row(row) if row else None


async def add_news_block(user_id: str, domain: str) -> bool:
    domain = (domain or "").strip().lower()
    if not domain:
        return False
    async with aiosqlite.connect(core.DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO news_blocklist (user_id, domain) VALUES (?, ?)",
            (user_id, domain),
        )
        await db.commit()
    return True


async def remove_news_block(user_id: str, domain: str) -> bool:
    domain = (domain or "").strip().lower()
    if not domain:
        return False
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM news_blocklist WHERE user_id = ? AND domain = ?",
            (user_id, domain),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_news_blocks(user_id: str) -> list[str]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT domain FROM news_blocklist WHERE user_id = ? ORDER BY domain",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


# ---- error log (eccezioni: pannello /logs + notifica HA) ----

async def add_error_log(source: str, level: str, message: str, traceback: str = "") -> None:
    async with aiosqlite.connect(core.DB_PATH) as db:
        await db.execute(
            "INSERT INTO error_log (source, level, message, traceback) VALUES (?, ?, ?, ?)",
            (source, level, message, traceback),
        )
        # retention: tieni solo gli ultimi 500
        await db.execute(
            "DELETE FROM error_log WHERE id NOT IN "
            "(SELECT id FROM error_log ORDER BY id DESC LIMIT 500)"
        )
        await db.commit()


async def get_error_logs(limit: int = 100) -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, ts, source, level, message, traceback FROM error_log "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        rows = await cursor.fetchall()
    cols = ("id", "ts", "source", "level", "message", "traceback")
    return [dict(zip(cols, r)) for r in rows]


async def clear_error_logs() -> int:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute("DELETE FROM error_log")
        await db.commit()
        return cursor.rowcount




