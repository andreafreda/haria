import aiosqlite
import os

DB_PATH = os.environ.get("DB_PATH", "/config/haria.db")
MAX_HISTORY = 10


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, key)
            );
            CREATE TABLE IF NOT EXISTS entity_cache (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                data TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                message TEXT NOT NULL,
                remind_at DATETIME,
                recurring TEXT,
                active INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        await db.commit()


async def get_history(user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT role, content FROM conversations
               WHERE user_id = ?
               ORDER BY timestamp DESC LIMIT ?""",
            (user_id, MAX_HISTORY),
        )
        rows = await cursor.fetchall()
    return [{"role": r, "content": c} for r, c in reversed(rows)]


async def save_turn(user_id: str, role: str, content: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await db.commit()


async def clear_history(user_id: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        await db.commit()


async def get_notes(user_id: str) -> dict[str, str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT key, value FROM notes WHERE user_id = ?", (user_id,)
        )
        rows = await cursor.fetchall()
    return {k: v for k, v in rows}


async def save_note(user_id: str, key: str, value: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO notes (user_id, key, value, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (user_id, key, value),
        )
        await db.commit()


async def get_entity_cache() -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT data FROM entity_cache WHERE id = 1")
        row = await cursor.fetchone()
    return row[0] if row else None


async def clear_entity_cache():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM entity_cache WHERE id = 1")
        await db.commit()


async def save_entity_cache(data: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO entity_cache (id, data, updated_at)
               VALUES (1, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(id) DO UPDATE SET data=excluded.data, updated_at=CURRENT_TIMESTAMP""",
            (data,),
        )
        await db.commit()


def _reminder_row(r) -> dict:
    return {
        "id": r[0],
        "user_id": r[1],
        "message": r[2],
        "remind_at": r[3],
        "recurring": r[4],
    }


async def add_reminder(user_id: str, message: str, remind_at: str | None, recurring: str | None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO reminders (user_id, message, remind_at, recurring) VALUES (?, ?, ?, ?)",
            (user_id, message, remind_at, recurring),
        )
        await db.commit()
        rid = cursor.lastrowid
    return {"id": rid, "user_id": user_id, "message": message, "remind_at": remind_at, "recurring": recurring}


async def get_active_reminders() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, message, remind_at, recurring FROM reminders WHERE active = 1"
        )
        rows = await cursor.fetchall()
    return [_reminder_row(r) for r in rows]


async def get_user_reminders(user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, message, remind_at, recurring FROM reminders WHERE active = 1 AND user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [_reminder_row(r) for r in rows]


async def deactivate_reminder(reminder_id: int, user_id: str | None = None) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
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
