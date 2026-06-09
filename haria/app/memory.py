"""Persistenza HARIA: DB SQLite (/config/haria.db) via aiosqlite.

Schema + migrazioni leggere (PRAGMA table_info + ALTER) e tutte le funzioni
async di accesso dati: history/memoria/riassunti, promemoria, briefing news,
profili nutrizionali, pasti, piano settimanale, spesa, dispensa, food_cache,
ricerca full-text (FTS5).
"""
import aiosqlite
import os
from datetime import date, timedelta

DB_PATH = os.environ.get("DB_PATH", "/config/haria.db")
MAX_HISTORY = 10          # turni raw inviati a ogni richiesta
SUMMARY_BATCH = 20        # turni vecchi piegati nel summary per giro
_FTS_OK = False           # FTS5 disponibile (settato in init_db)


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
            CREATE TABLE IF NOT EXISTS meal_plan (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                meal_type TEXT NOT NULL,
                member TEXT NOT NULL DEFAULT '',
                items TEXT NOT NULL,
                recipe TEXT,
                servings INTEGER,
                kcal REAL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, meal_type, member)
            );
            CREATE TABLE IF NOT EXISTS hydration_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                member TEXT NOT NULL,
                ml REAL NOT NULL,
                logged_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS shopping_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                qty TEXT,
                category TEXT,
                checked INTEGER DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS pantry_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                qty TEXT,
                category TEXT,
                expires_on TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS food_cache (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                source TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS conv_summary (
                user_id TEXT PRIMARY KEY,
                summary TEXT NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS briefings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                topics TEXT NOT NULL,
                cron TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS news_blocklist (
                user_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, domain)
            );
            CREATE TABLE IF NOT EXISTS error_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                source TEXT,
                level TEXT,
                message TEXT NOT NULL,
                traceback TEXT
            );
            CREATE TABLE IF NOT EXISTS bollette (
                utility TEXT NOT NULL,
                metric TEXT NOT NULL,
                year INTEGER NOT NULL,
                month INTEGER NOT NULL,
                value REAL NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (utility, metric, year, month)
            );
        """)
        # migrazione: i briefing/blocklist creati dalla chat web avevano user_id
        # 'ha_chat_<chatid>' (non consegnabile via Telegram, int() crasha). Normalizza
        # al chat_id numerico così coincide con Telegram e col pannello web.
        await db.execute(
            "UPDATE briefings SET user_id = replace(user_id, 'ha_chat_', '') "
            "WHERE user_id LIKE 'ha_chat_%'"
        )
        await db.execute(
            "UPDATE news_blocklist SET user_id = replace(user_id, 'ha_chat_', '') "
            "WHERE user_id LIKE 'ha_chat_%'"
        )
        await db.execute(
            "UPDATE reminders SET user_id = replace(user_id, 'ha_chat_', '') "
            "WHERE user_id LIKE 'ha_chat_%'"
        )
        # migrazioni leggere: aggiungi colonne se mancano
        for table, col, ddl in [
            ("briefings", "num_news", "ALTER TABLE briefings ADD COLUMN num_news INTEGER DEFAULT 5"),
            ("meal_plan", "kcal", "ALTER TABLE meal_plan ADD COLUMN kcal REAL"),
            ("shopping_items", "price", "ALTER TABLE shopping_items ADD COLUMN price REAL"),
            ("meals", "fiber_g", "ALTER TABLE meals ADD COLUMN fiber_g REAL"),
            ("meals", "sugar_g", "ALTER TABLE meals ADD COLUMN sugar_g REAL"),
            ("meals", "sat_fat_g", "ALTER TABLE meals ADD COLUMN sat_fat_g REAL"),
            ("meals", "sodium_mg", "ALTER TABLE meals ADD COLUMN sodium_mg REAL"),
            ("meal_items", "fiber_g", "ALTER TABLE meal_items ADD COLUMN fiber_g REAL"),
            ("meal_items", "sugar_g", "ALTER TABLE meal_items ADD COLUMN sugar_g REAL"),
            ("meal_items", "sat_fat_g", "ALTER TABLE meal_items ADD COLUMN sat_fat_g REAL"),
            ("meal_items", "sodium_mg", "ALTER TABLE meal_items ADD COLUMN sodium_mg REAL"),
            ("meals", "vit_c_mg", "ALTER TABLE meals ADD COLUMN vit_c_mg REAL"),
            ("meals", "vit_d_ug", "ALTER TABLE meals ADD COLUMN vit_d_ug REAL"),
            ("meals", "iron_mg", "ALTER TABLE meals ADD COLUMN iron_mg REAL"),
            ("meals", "calcium_mg", "ALTER TABLE meals ADD COLUMN calcium_mg REAL"),
            ("meals", "potassium_mg", "ALTER TABLE meals ADD COLUMN potassium_mg REAL"),
            ("meals", "magnesium_mg", "ALTER TABLE meals ADD COLUMN magnesium_mg REAL"),
            ("meal_items", "vit_c_mg", "ALTER TABLE meal_items ADD COLUMN vit_c_mg REAL"),
            ("meal_items", "vit_d_ug", "ALTER TABLE meal_items ADD COLUMN vit_d_ug REAL"),
            ("meal_items", "iron_mg", "ALTER TABLE meal_items ADD COLUMN iron_mg REAL"),
            ("meal_items", "calcium_mg", "ALTER TABLE meal_items ADD COLUMN calcium_mg REAL"),
            ("meal_items", "potassium_mg", "ALTER TABLE meal_items ADD COLUMN potassium_mg REAL"),
            ("meal_items", "magnesium_mg", "ALTER TABLE meal_items ADD COLUMN magnesium_mg REAL"),
        ]:
            cur = await db.execute(f"PRAGMA table_info({table})")
            cols = [r[1] for r in await cur.fetchall()]
            if col not in cols:
                await db.execute(ddl)
        # migrazione meal_plan: aggiungi member + UNIQUE(date,meal_type,member).
        # SQLite non droppa constraint -> rebuild tabella.
        cur = await db.execute("PRAGMA table_info(meal_plan)")
        mp_cols = [r[1] for r in await cur.fetchall()]
        if "member" not in mp_cols:
            await db.executescript("""
                CREATE TABLE meal_plan_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    meal_type TEXT NOT NULL,
                    member TEXT NOT NULL DEFAULT '',
                    items TEXT NOT NULL,
                    recipe TEXT,
                    servings INTEGER,
                    kcal REAL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date, meal_type, member)
                );
                INSERT INTO meal_plan_new (id, date, meal_type, member, items, recipe, servings, kcal, updated_at)
                    SELECT id, date, meal_type, '', items, recipe, servings, kcal, updated_at FROM meal_plan;
                DROP TABLE meal_plan;
                ALTER TABLE meal_plan_new RENAME TO meal_plan;
            """)
        # FTS5 per recall keyword su note + conversazioni (best-effort: se il
        # build sqlite non ha FTS5, recall degrada a vuoto senza rompere nulla).
        global _FTS_OK
        try:
            await db.executescript("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                    user_id UNINDEXED, kind UNINDEXED, content
                );
            """)
            # backfill una tantum (tabella vuota = primo avvio con FTS)
            cur = await db.execute("SELECT count(*) FROM memory_fts")
            if (await cur.fetchone())[0] == 0:
                await db.execute(
                    "INSERT INTO memory_fts (user_id, kind, content) "
                    "SELECT user_id, 'conv', content FROM conversations"
                )
                await db.execute(
                    "INSERT INTO memory_fts (user_id, kind, content) "
                    "SELECT user_id, 'note', key || ': ' || value FROM notes"
                )
            _FTS_OK = True
        except Exception:
            _FTS_OK = False
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
        if _FTS_OK and content:
            await db.execute(
                "INSERT INTO memory_fts (user_id, kind, content) VALUES (?, 'conv', ?)",
                (user_id, content),
            )
        await db.commit()


async def clear_history(user_id: str):
    """Cancella history raw + summary dell'utente. FTS resta (recall storico)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM conv_summary WHERE user_id = ?", (user_id,))
        await db.commit()


# ---- memoria a lungo termine: summary conversazione ----

async def count_history(user_id: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT count(*) FROM conversations WHERE user_id = ?", (user_id,)
        )
        return (await cur.fetchone())[0]


async def get_summary(user_id: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT summary FROM conv_summary WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
    return row[0] if row else None


async def set_summary(user_id: str, summary: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO conv_summary (user_id, summary, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(user_id) DO UPDATE SET
                   summary=excluded.summary, updated_at=CURRENT_TIMESTAMP""",
            (user_id, summary),
        )
        await db.commit()


async def get_old_turns(user_id: str, keep: int = MAX_HISTORY,
                        batch: int = SUMMARY_BATCH) -> list[dict]:
    """Turni più vecchi della finestra recente `keep`, dal più vecchio.
    Ritorna [{id, role, content}] (max `batch`). Vuoto se niente da riassumere."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """SELECT id, role, content FROM conversations
               WHERE user_id = ? AND id NOT IN (
                   SELECT id FROM conversations WHERE user_id = ?
                   ORDER BY id DESC LIMIT ?
               )
               ORDER BY id ASC LIMIT ?""",
            (user_id, user_id, keep, batch),
        )
        rows = await cur.fetchall()
    return [{"id": i, "role": r, "content": c} for i, r, c in rows]


async def delete_turns(ids: list[int]):
    if not ids:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "DELETE FROM conversations WHERE id = ?", [(i,) for i in ids]
        )
        await db.commit()


async def search_memory(user_id: str, query: str, limit: int = 5) -> list[str]:
    """Recall keyword via FTS5 su note + conversazioni passate dell'utente.
    Ritorna snippet di contenuto. Vuoto se FTS assente o nessun match."""
    if not _FTS_OK:
        return []
    import re
    terms = re.findall(r"\w+", (query or "").lower())
    if not terms:
        return []
    match = " OR ".join(terms)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            cur = await db.execute(
                """SELECT content FROM memory_fts
                   WHERE user_id = ? AND memory_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (user_id, match, limit),
            )
            rows = await cur.fetchall()
        except Exception:
            return []
    return [r[0] for r in rows]


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
        if _FTS_OK:
            # rimuovi vecchie righe FTS della stessa nota (key) per evitare duplicati su update
            await db.execute(
                "DELETE FROM memory_fts WHERE user_id = ? AND kind = 'note' AND content LIKE ?",
                (user_id, f"{key}: %"),
            )
            await db.execute(
                "INSERT INTO memory_fts (user_id, kind, content) VALUES (?, 'note', ?)",
                (user_id, f"{key}: {value}"),
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO briefings (user_id, topics, cron, num_news) VALUES (?, ?, ?, ?)",
            (user_id, topics, cron, int(num_news)),
        )
        await db.commit()
        bid = cursor.lastrowid
    return {"id": bid, "user_id": user_id, "topics": topics, "cron": cron, "num_news": int(num_news)}


async def get_active_briefings() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, topics, cron, num_news FROM briefings WHERE active = 1"
        )
        rows = await cursor.fetchall()
    return [_briefing_row(r) for r in rows]


async def get_user_briefings(user_id: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, user_id, topics, cron, num_news FROM briefings WHERE active = 1 AND user_id = ?",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [_briefing_row(r) for r in rows]


async def get_briefing(briefing_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM news_blocklist WHERE user_id = ? AND domain = ?",
            (user_id, domain),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_news_blocks(user_id: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT domain FROM news_blocklist WHERE user_id = ? ORDER BY domain",
            (user_id,),
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


# ---- error log (eccezioni: pannello /logs + notifica HA) ----

async def add_error_log(source: str, level: str, message: str, traceback: str = "") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, ts, source, level, message, traceback FROM error_log "
            "ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        rows = await cursor.fetchall()
    cols = ("id", "ts", "source", "level", "message", "traceback")
    return [dict(zip(cols, r)) for r in rows]


async def clear_error_logs() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM error_log")
        await db.commit()
        return cursor.rowcount


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


async def delete_profile(member: str) -> bool:
    """Cancella il profilo dieta di un membro. Ritorna True se cancellato."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM diet_profiles WHERE member = ?",
            (member,),
        )
        await db.commit()
        return cursor.rowcount > 0


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
            """SELECT id, weight_kg, bmi, logged_at FROM weight_log
               WHERE member = ? ORDER BY logged_at DESC LIMIT ?""",
            (member, limit),
        )
        rows = await cursor.fetchall()
    return [{"id": i, "weight_kg": w, "bmi": b, "logged_at": t} for i, w, b, t in rows]


async def update_weight(weight_id: int, weight_kg: float | None = None,
                        bmi: float | None = None) -> bool:
    """Corregge una misura di peso per id. Ritorna True se modificata."""
    sets: list[str] = []
    params: list = []
    if weight_kg is not None:
        sets.append("weight_kg = ?"); params.append(weight_kg)
    if bmi is not None:
        sets.append("bmi = ?"); params.append(bmi)
    if not sets:
        return False
    params.append(int(weight_id))
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE weight_log SET {', '.join(sets)} WHERE id = ?", params
        )
        await db.commit()
        return cursor.rowcount > 0


async def delete_weight(weight_id: int) -> bool:
    """Cancella una misura di peso per id. Ritorna True se cancellata."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM weight_log WHERE id = ?", (int(weight_id),))
        await db.commit()
        return cursor.rowcount > 0


async def get_weight_stats(member: str, days: int = 30) -> dict | None:
    """Statistiche peso su finestra `days`: ultimo, min, max, delta (ultimo-primo), n."""
    since = (date.today() - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT weight_kg, bmi, logged_at FROM weight_log
               WHERE member = ? AND logged_at >= ?
               ORDER BY logged_at ASC""",
            (member, since),
        )
        rows = await cursor.fetchall()
    if not rows:
        # nessun log nella finestra: prova ultimo assoluto
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute(
                """SELECT weight_kg, bmi, logged_at FROM weight_log
                   WHERE member = ? ORDER BY logged_at DESC LIMIT 1""",
                (member,),
            )
            last = await cursor.fetchone()
        if not last:
            return None
        w, b, t = last
        return {"latest": w, "latest_bmi": b, "latest_at": t,
                "min": w, "max": w, "delta": 0.0, "count": 1, "days": days}
    weights = [r[0] for r in rows]
    first_w = weights[0]
    last_w, last_b, last_t = rows[-1]
    return {
        "latest": last_w, "latest_bmi": last_b, "latest_at": last_t,
        "min": min(weights), "max": max(weights),
        "delta": round(last_w - first_w, 1), "count": len(rows), "days": days,
    }


# ---- food_diary: pasti ----

async def add_meal(member: str, meal_type: str, description: str, totals: dict,
                   items: list[dict], eaten_at: str | None, logged_by: str | None) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO meals
               (member, meal_type, description, kcal_total, protein_g, carbs_g, fat_g,
                fiber_g, sugar_g, sat_fat_g, sodium_mg,
                vit_c_mg, vit_d_ug, iron_mg, calcium_mg, potassium_mg, magnesium_mg,
                eaten_at, logged_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP), ?)""",
            (member, meal_type, description,
             totals.get("kcal_total"), totals.get("protein_g"),
             totals.get("carbs_g"), totals.get("fat_g"),
             totals.get("fiber_g"), totals.get("sugar_g"),
             totals.get("sat_fat_g"), totals.get("sodium_mg"),
             totals.get("vit_c_mg"), totals.get("vit_d_ug"), totals.get("iron_mg"),
             totals.get("calcium_mg"), totals.get("potassium_mg"), totals.get("magnesium_mg"),
             eaten_at, logged_by),
        )
        meal_id = cursor.lastrowid
        for it in items or []:
            await db.execute(
                """INSERT INTO meal_items
                   (meal_id, name, grams, kcal, protein_g, carbs_g, fat_g,
                    fiber_g, sugar_g, sat_fat_g, sodium_mg,
                    vit_c_mg, vit_d_ug, iron_mg, calcium_mg, potassium_mg, magnesium_mg)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (meal_id, it.get("name"), it.get("grams"), it.get("kcal"),
                 it.get("protein_g"), it.get("carbs_g"), it.get("fat_g"),
                 it.get("fiber_g"), it.get("sugar_g"),
                 it.get("sat_fat_g"), it.get("sodium_mg"),
                 it.get("vit_c_mg"), it.get("vit_d_ug"), it.get("iron_mg"),
                 it.get("calcium_mg"), it.get("potassium_mg"), it.get("magnesium_mg")),
            )
        await db.commit()
    return {"id": meal_id, "member": member, "meal_type": meal_type}


async def update_meal(meal_id: int, meal_type: str | None = None, description: str | None = None,
                      totals: dict | None = None, eaten_at: str | None = None) -> bool:
    """Modifica un pasto registrato. Solo i campi passati vengono aggiornati. Ritorna True se modificato."""
    sets: list[str] = []
    params: list = []
    if meal_type is not None:
        sets.append("meal_type = ?"); params.append(meal_type)
    if description is not None:
        sets.append("description = ?"); params.append(description)
    if eaten_at is not None:
        sets.append("eaten_at = ?"); params.append(eaten_at)
    if totals:
        for col, key in (("kcal_total", "kcal_total"), ("protein_g", "protein_g"),
                         ("carbs_g", "carbs_g"), ("fat_g", "fat_g")):
            if totals.get(key) is not None:
                sets.append(f"{col} = ?"); params.append(totals[key])
    if not sets:
        return False
    params.append(int(meal_id))
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(f"UPDATE meals SET {', '.join(sets)} WHERE id = ?", params)
        await db.commit()
        return cursor.rowcount > 0


async def delete_meal(meal_id: int) -> bool:
    """Cancella un pasto registrato (e i suoi meal_items). Ritorna True se cancellato."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM meal_items WHERE meal_id = ?", (int(meal_id),))
        cursor = await db.execute("DELETE FROM meals WHERE id = ?", (int(meal_id),))
        await db.commit()
        return cursor.rowcount > 0


async def get_meals(member: str, date_from: str | None = None, date_to: str | None = None) -> list[dict]:
    q = ("SELECT id, meal_type, description, kcal_total, protein_g, carbs_g, fat_g, eaten_at "
         "FROM meals WHERE member = ?")
    params: list = [member]
    if date_from:
        q += " AND DATE(eaten_at) >= DATE(?)"
        params.append(date_from)
    if date_to:
        q += " AND DATE(eaten_at) <= DATE(?)"
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


async def get_logged_days(days_back: int = 30) -> list[dict]:
    """Giorni con pasti registrati negli ultimi N giorni. Ritorna [{day, members, meals, kcal}] desc."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT DATE(eaten_at) AS d,
                      COUNT(DISTINCT member) AS members,
                      COUNT(*) AS meals,
                      COALESCE(SUM(kcal_total), 0) AS kcal
               FROM meals
               WHERE DATE(eaten_at) >= DATE('now', ?)
               GROUP BY d ORDER BY d DESC""",
            (f"-{int(days_back)} days",),
        )
        rows = await cursor.fetchall()
    return [{"day": r[0], "members": r[1], "meals": r[2], "kcal": round(r[3], 1)} for r in rows]


# ---- food_diary: piano settimanale ----

async def set_plan_meal(date: str, meal_type: str, items: str,
                        recipe: str | None = None, servings: int | None = None,
                        kcal: float | None = None, member: str | None = None):
    """Inserisce o sostituisce un pasto del piano (chiave date+meal_type+member).

    member vuoto/None = piano comune. member valorizzato = override personale.
    """
    member = (member or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO meal_plan (date, meal_type, member, items, recipe, servings, kcal, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(date, meal_type, member) DO UPDATE SET
                 items=excluded.items, recipe=excluded.recipe,
                 servings=excluded.servings, kcal=excluded.kcal,
                 updated_at=CURRENT_TIMESTAMP""",
            (date, meal_type, member, items, recipe, servings, kcal),
        )
        await db.commit()


async def delete_plan_meal(date: str, meal_type: str, member: str | None = None) -> int:
    """Elimina un pasto del piano (chiave date+meal_type+member).

    member vuoto/None = piano comune. Ritorna numero righe eliminate.
    """
    member = (member or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM meal_plan WHERE date = ? AND meal_type = ? AND member = ?",
            (date, meal_type, member),
        )
        await db.commit()
        return cursor.rowcount


async def get_meal_plan(date_from: str, date_to: str) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT date, meal_type, member, items, recipe, servings, kcal FROM meal_plan
               WHERE date >= ? AND date <= ? ORDER BY date, meal_type, member""",
            (date_from, date_to),
        )
        rows = await cursor.fetchall()
    return [
        {"date": r[0], "meal_type": r[1], "member": r[2], "items": r[3],
         "recipe": r[4], "servings": r[5], "kcal": r[6]}
        for r in rows
    ]


async def get_day_totals(member: str, day: str) -> dict:
    """Somma kcal/macro dei pasti di un membro in un giorno (YYYY-MM-DD)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT COALESCE(SUM(kcal_total),0), COALESCE(SUM(protein_g),0),
                      COALESCE(SUM(carbs_g),0), COALESCE(SUM(fat_g),0),
                      COALESCE(SUM(fiber_g),0), COALESCE(SUM(sugar_g),0),
                      COALESCE(SUM(sat_fat_g),0), COALESCE(SUM(sodium_mg),0),
                      COALESCE(SUM(vit_c_mg),0), COALESCE(SUM(vit_d_ug),0),
                      COALESCE(SUM(iron_mg),0), COALESCE(SUM(calcium_mg),0),
                      COALESCE(SUM(potassium_mg),0), COALESCE(SUM(magnesium_mg),0), COUNT(*)
               FROM meals WHERE member = ? AND DATE(eaten_at) = ?""",
            (member, day),
        )
        r = await cursor.fetchone()
    return {"kcal": round(r[0], 1), "protein_g": round(r[1], 1),
            "carbs_g": round(r[2], 1), "fat_g": round(r[3], 1),
            "fiber_g": round(r[4], 1), "sugar_g": round(r[5], 1),
            "sat_fat_g": round(r[6], 1), "sodium_mg": round(r[7], 1),
            "vit_c_mg": round(r[8], 1), "vit_d_ug": round(r[9], 1),
            "iron_mg": round(r[10], 1), "calcium_mg": round(r[11], 1),
            "potassium_mg": round(r[12], 1), "magnesium_mg": round(r[13], 1), "meals": r[14]}


# ---- food_diary: idratazione ----

async def add_hydration(member: str, ml: float) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO hydration_log (member, ml) VALUES (?, ?)", (member, ml)
        )
        await db.commit()
        hid = cursor.lastrowid
    return {"id": hid, "member": member, "ml": ml}


async def delete_last_hydration(member: str) -> bool:
    """Annulla l'ultimo log idratazione del membro oggi. Ritorna True se cancellato."""
    async with aiosqlite.connect(DB_PATH) as db:
        row = await (await db.execute(
            """SELECT id FROM hydration_log
               WHERE member = ? AND DATE(logged_at) = DATE('now', 'localtime')
               ORDER BY logged_at DESC LIMIT 1""",
            (member,),
        )).fetchone()
        if not row:
            return False
        cursor = await db.execute("DELETE FROM hydration_log WHERE id = ?", (row[0],))
        await db.commit()
        return cursor.rowcount > 0


async def get_hydration_day(member: str, day: str) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT COALESCE(SUM(ml),0), COUNT(*) FROM hydration_log
               WHERE member = ? AND DATE(logged_at) = ?""",
            (member, day),
        )
        r = await cursor.fetchone()
    return {"ml_total": round(r[0], 1), "count": r[1]}


# ---- food_diary: lista spesa ----

async def add_shopping_items(items: list[dict]) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        for it in items or []:
            await db.execute(
                "INSERT INTO shopping_items (name, qty, category, price) VALUES (?, ?, ?, ?)",
                (it.get("name"), it.get("qty"), it.get("category"), it.get("price")),
            )
        await db.commit()
    return len(items or [])


async def get_shopping_list(include_checked: bool = False) -> list[dict]:
    q = "SELECT id, name, qty, category, checked, price FROM shopping_items"
    if not include_checked:
        q += " WHERE checked = 0"
    q += " ORDER BY category, name"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(q)
        rows = await cursor.fetchall()
    return [{"id": r[0], "name": r[1], "qty": r[2], "category": r[3],
             "checked": bool(r[4]), "price": r[5]} for r in rows]


async def set_shopping_price(name: str, price: float) -> bool:
    """Imposta il prezzo (€) di una voce spesa per nome (match parziale)."""
    if not (name or "").strip():
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE shopping_items SET price = ? WHERE LOWER(name) LIKE LOWER(?)",
            (price, f"%{name}%"),
        )
        await db.commit()
        return cursor.rowcount > 0


async def remove_shopping_item(name: str) -> int:
    """Rimuove voci spesa per nome (match parziale). Ritorna righe eliminate."""
    if not (name or "").strip():
        return 0
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM shopping_items WHERE LOWER(name) LIKE LOWER(?)",
            (f"%{name}%",),
        )
        await db.commit()
        return cursor.rowcount


async def get_shopping_cost(include_checked: bool = True) -> dict:
    """Costo totale lista spesa: somma price. Ritorna {total, priced, missing, count}."""
    q = "SELECT price FROM shopping_items"
    if not include_checked:
        q += " WHERE checked = 0"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(q)
        rows = await cursor.fetchall()
    total = round(sum(r[0] for r in rows if r[0] is not None), 2)
    priced = sum(1 for r in rows if r[0] is not None)
    return {"total": total, "priced": priced, "missing": len(rows) - priced, "count": len(rows)}


async def check_shopping_item(name: str) -> bool:
    if not (name or "").strip():
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE shopping_items SET checked = 1 WHERE checked = 0 AND LOWER(name) LIKE LOWER(?)",
            (f"%{name}%",),
        )
        await db.commit()
        return cursor.rowcount > 0


async def toggle_shopping_item(item_id: int) -> bool:
    """Inverte lo stato checked di una voce spesa per id. Ritorna True se trovata."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE shopping_items SET checked = 1 - checked WHERE id = ?",
            (int(item_id),),
        )
        await db.commit()
        return cursor.rowcount > 0


async def clear_shopping_list(only_checked: bool = False) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        if only_checked:
            cursor = await db.execute("DELETE FROM shopping_items WHERE checked = 1")
        else:
            cursor = await db.execute("DELETE FROM shopping_items")
        await db.commit()
        return cursor.rowcount


# ---- food_diary: dispensa / scorte ----

async def add_pantry_items(items: list[dict]) -> int:
    """Aggiunge voci alla dispensa. Ogni item: name, qty?, category?, expires_on?"""
    n = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for it in items:
            name = (it.get("name") or "").strip()
            if not name:
                continue
            await db.execute(
                """INSERT INTO pantry_items (name, qty, category, expires_on, updated_at)
                   VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (name, it.get("qty"), it.get("category"), it.get("expires_on")),
            )
            n += 1
        await db.commit()
    return n


async def get_pantry(category: str | None = None) -> list[dict]:
    q = "SELECT id, name, qty, category, expires_on FROM pantry_items"
    params: list = []
    if category:
        q += " WHERE category = ?"
        params.append(category)
    q += " ORDER BY (expires_on IS NULL), expires_on, name"
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(q, params)
        rows = await cursor.fetchall()
    return [{"id": r[0], "name": r[1], "qty": r[2], "category": r[3], "expires_on": r[4]}
            for r in rows]


async def get_pantry_expiring(within_days: int = 3) -> list[dict]:
    """Voci con scadenza entro N giorni (o già scadute)."""
    limit = (date.today() + timedelta(days=within_days)).isoformat()
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT id, name, qty, category, expires_on FROM pantry_items
               WHERE expires_on IS NOT NULL AND expires_on <= ?
               ORDER BY expires_on""",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [{"id": r[0], "name": r[1], "qty": r[2], "category": r[3], "expires_on": r[4]}
            for r in rows]


async def consume_pantry_item(name: str) -> int:
    """Rimuove dalla dispensa la voce per nome (match case-insensitive)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM pantry_items WHERE LOWER(name) = ?", ((name or "").strip().lower(),)
        )
        await db.commit()
        return cursor.rowcount


async def update_pantry_item(name: str, qty: str | None = None,
                             category: str | None = None,
                             expires_on: str | None = None) -> int:
    """Modifica voci dispensa per nome (match parziale). Solo campi passati.
    Ritorna righe modificate."""
    sets: list[str] = []
    params: list = []
    if qty is not None:
        sets.append("qty = ?"); params.append(qty)
    if category is not None:
        sets.append("category = ?"); params.append(category)
    if expires_on is not None:
        sets.append("expires_on = ?"); params.append(expires_on or None)
    if not sets:
        return 0
    if not (name or "").strip():
        return 0
    sets.append("updated_at = CURRENT_TIMESTAMP")
    params.append(f"%{name}%")
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            f"UPDATE pantry_items SET {', '.join(sets)} WHERE LOWER(name) LIKE LOWER(?)",
            params,
        )
        await db.commit()
        return cursor.rowcount


async def clear_pantry() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM pantry_items")
        await db.commit()
        return cursor.rowcount


# ---- food_diary: cache valori nutrizionali ----

FOOD_CACHE_TTL_DAYS = 90


async def get_food_cache(key: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT data, source FROM food_cache WHERE key = ? "
            "AND updated_at > datetime('now', ?)",
            (key.lower(), f"-{FOOD_CACHE_TTL_DAYS} days"),
        )
        row = await cursor.fetchone()
    if not row:
        return None
    import json as _json
    d = _json.loads(row[0])
    d["_source"] = row[1]
    return d


async def set_food_cache(key: str, data: dict, source: str):
    import json as _json
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO food_cache (key, data, source, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(key) DO UPDATE SET data=excluded.data,
                 source=excluded.source, updated_at=CURRENT_TIMESTAMP""",
            (key.lower(), _json.dumps(data, ensure_ascii=False), source),
        )
        await db.execute(
            "DELETE FROM food_cache WHERE updated_at <= datetime('now', ?)",
            (f"-{FOOD_CACHE_TTL_DAYS} days",),
        )
        await db.commit()


# ---- webpanel: query lettura ----

async def list_profiles() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT member, sex, age, height_cm, weight_kg, goal, activity_level,
                      kcal_target, bmi FROM diet_profiles ORDER BY member"""
        )
        rows = await cursor.fetchall()
    cols = ("member", "sex", "age", "height_cm", "weight_kg", "goal",
            "activity_level", "kcal_target", "bmi")
    return [dict(zip(cols, r)) for r in rows]


async def export_meals(date_from: str, date_to: str) -> list[dict]:
    """Tutti i pasti di tutti i membri in un intervallo (per export CSV)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT eaten_at, member, meal_type, description, kcal_total,
                      protein_g, carbs_g, fat_g, logged_by
               FROM meals WHERE DATE(eaten_at) >= ? AND DATE(eaten_at) <= ?
               ORDER BY eaten_at""",
            (date_from, date_to),
        )
        rows = await cursor.fetchall()
    cols = ("eaten_at", "member", "meal_type", "description", "kcal_total",
            "protein_g", "carbs_g", "fat_g", "logged_by")
    return [dict(zip(cols, r)) for r in rows]


async def list_members_with_meals(day: str) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT member FROM meals WHERE DATE(eaten_at) = ? ORDER BY member",
            (day,),
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


# ---- Bollette (consumi/costi utenze) -------------------------------------
# Storage persistente per la dashboard Consumi: una riga per (utility, metric,
# year, month). metric: 'kwh'|'m3' per consumo, 'costo' per €. Fonte di verita'
# unica: HARIA pubblica i valori su HA via MQTT (mqtt_pub), niente input_text.

async def set_bolletta(utility: str, metric: str, year: int, month: int, value: float):
    """Upsert singolo mese."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO bollette (utility, metric, year, month, value, updated_at)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(utility, metric, year, month)
               DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
            (utility, metric, int(year), int(month), float(value)),
        )
        await db.commit()


async def set_bolletta_range(utility: str, metric: str, year: int,
                             m_start: int, m_end: int, total: float):
    """Distribuisce `total` equamente sui mesi [m_start, m_end] (1-based)."""
    m_start, m_end = int(m_start), int(m_end)
    if m_end < m_start:
        m_end = m_start
    n = m_end - m_start + 1
    per = round(float(total) / n, 1)
    async with aiosqlite.connect(DB_PATH) as db:
        for mth in range(m_start, m_end + 1):
            await db.execute(
                """INSERT INTO bollette (utility, metric, year, month, value, updated_at)
                   VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(utility, metric, year, month)
                   DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                (utility, metric, int(year), mth, per),
            )
        await db.commit()


async def get_bolletta_csv(utility: str, metric: str, year: int) -> str:
    """Ritorna i 12 valori mensili come CSV (0 per mesi mancanti)."""
    vals = [0.0] * 12
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT month, value FROM bollette WHERE utility=? AND metric=? AND year=?",
            (utility, metric, int(year)),
        )
        for mth, val in await cursor.fetchall():
            if 1 <= mth <= 12:
                vals[mth - 1] = val
    return ",".join(_fmt_num(v) for v in vals)


def _fmt_num(v: float) -> str:
    """Intero senza decimali se intero, altrimenti con decimali compatti."""
    return str(int(v)) if float(v).is_integer() else str(v)


async def get_bolletta_existing_range(utility: str, metric: str, year: int,
                                      m_start: int, m_end: int) -> list[tuple]:
    """Mesi gia' valorizzati (!=0) nel range [m_start, m_end]. [(month, value)]."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            """SELECT month, value FROM bollette
               WHERE utility=? AND metric=? AND year=? AND month>=? AND month<=? AND value!=0
               ORDER BY month""",
            (utility, metric, int(year), int(m_start), int(m_end)),
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def get_bolletta_years(utility: str, metric: str) -> list[int]:
    """Anni con almeno un valore per (utility, metric)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT year FROM bollette WHERE utility=? AND metric=? ORDER BY year",
            (utility, metric),
        )
        return [r[0] for r in await cursor.fetchall()]


async def bollette_is_empty() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM bollette LIMIT 1")
        return (await cursor.fetchone()) is None


async def seed_bollette(rows: list[tuple]):
    """Bulk insert iniziale. rows = [(utility, metric, year, month, value), ...]."""
    if not rows:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            """INSERT INTO bollette (utility, metric, year, month, value)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(utility, metric, year, month) DO UPDATE SET value=excluded.value""",
            rows,
        )
        await db.commit()
