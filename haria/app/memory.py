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
            CREATE TABLE IF NOT EXISTS diet_profiles (
                member TEXT PRIMARY KEY,
                sex TEXT,
                age INTEGER,
                height_cm REAL,
                weight_kg REAL,
                goal TEXT,
                activity_level TEXT,
                kcal_target INTEGER,
                bmi REAL,
                allergies TEXT,
                preferences TEXT,
                restrictions TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS weight_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member TEXT NOT NULL,
                weight_kg REAL NOT NULL,
                bmi REAL,
                logged_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS meals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member TEXT NOT NULL,
                meal_type TEXT NOT NULL,
                description TEXT NOT NULL,
                kcal_total REAL,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL,
                eaten_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                logged_by TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS meal_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meal_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                grams REAL,
                kcal REAL,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL
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


# ---- food_diary: profili ----

_PROFILE_FIELDS = (
    "member", "sex", "age", "height_cm", "weight_kg", "goal",
    "activity_level", "kcal_target", "bmi", "allergies", "preferences", "restrictions",
)


def _profile_row(r) -> dict:
    return dict(zip(_PROFILE_FIELDS, r))


async def get_profile(member: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT member, sex, age, height_cm, weight_kg, goal, activity_level,
                      kcal_target, bmi, allergies, preferences, restrictions
               FROM diet_profiles WHERE member = ?""",
            (member,),
        )
        row = await cursor.fetchone()
    return _profile_row(row) if row else None


async def upsert_profile(member: str, fields: dict):
    """Inserisce o aggiorna un profilo. Solo i campi presenti in `fields` vengono toccati."""
    cols = [k for k in fields if k in _PROFILE_FIELDS and k != "member"]
    async with aiosqlite.connect(DB_PATH) as db:
        exists = await (await db.execute(
            "SELECT 1 FROM diet_profiles WHERE member = ?", (member,)
        )).fetchone()
        if exists:
            if cols:
                sets = ", ".join(f"{c} = ?" for c in cols) + ", updated_at = CURRENT_TIMESTAMP"
                await db.execute(
                    f"UPDATE diet_profiles SET {sets} WHERE member = ?",
                    [fields[c] for c in cols] + [member],
                )
        else:
            allcols = ["member"] + cols
            placeholders = ", ".join("?" for _ in allcols)
            await db.execute(
                f"INSERT INTO diet_profiles ({', '.join(allcols)}) VALUES ({placeholders})",
                [member] + [fields[c] for c in cols],
            )
        await db.commit()


# ---- food_diary: peso ----

async def add_weight(member: str, weight_kg: float, bmi: float | None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO weight_log (member, weight_kg, bmi) VALUES (?, ?, ?)",
            (member, weight_kg, bmi),
        )
        await db.commit()
        wid = cursor.lastrowid
    return {"id": wid, "member": member, "weight_kg": weight_kg, "bmi": bmi}


async def get_weight_history(member: str, limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT weight_kg, bmi, logged_at FROM weight_log
               WHERE member = ? ORDER BY logged_at DESC LIMIT ?""",
            (member, limit),
        )
        rows = await cursor.fetchall()
    return [{"weight_kg": w, "bmi": b, "logged_at": t} for w, b, t in rows]


# ---- food_diary: pasti ----

async def add_meal(member: str, meal_type: str, description: str, totals: dict,
                   items: list[dict], eaten_at: str | None, logged_by: str | None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO meals
               (member, meal_type, description, kcal_total, protein_g, carbs_g, fat_g, eaten_at, logged_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)""",
            (member, meal_type, description,
             totals.get("kcal_total"), totals.get("protein_g"),
             totals.get("carbs_g"), totals.get("fat_g"), eaten_at, logged_by),
        )
        meal_id = cursor.lastrowid
        for it in items or []:
            await db.execute(
                """INSERT INTO meal_items (meal_id, name, grams, kcal, protein_g, carbs_g, fat_g)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (meal_id, it.get("name"), it.get("grams"), it.get("kcal"),
                 it.get("protein_g"), it.get("carbs_g"), it.get("fat_g")),
            )
        await db.commit()
    return {"id": meal_id, "member": member, "meal_type": meal_type}


async def get_meals(member: str, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    q = ("SELECT id, meal_type, description, kcal_total, protein_g, carbs_g, fat_g, eaten_at "
         "FROM meals WHERE member = ?")
    params: list = [member]
    if date_from:
        q += " AND eaten_at >= ?"
        params.append(date_from)
    if date_to:
        q += " AND eaten_at <= ?"
        params.append(date_to)
    q += " ORDER BY eaten_at DESC LIMIT 50"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(q, params)
        rows = await cursor.fetchall()
    return [
        {"id": r[0], "meal_type": r[1], "description": r[2], "kcal_total": r[3],
         "protein_g": r[4], "carbs_g": r[5], "fat_g": r[6], "eaten_at": r[7]}
        for r in rows
    ]
