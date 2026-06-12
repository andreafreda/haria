"""Persistenza HARIA: DB SQLite (/config/haria.db) via aiosqlite.

Schema + migrazioni leggere (PRAGMA table_info + ALTER) e tutte le funzioni
async di accesso dati: history/memoria/riassunti, promemoria, briefing news,
profili nutrizionali, pasti, piano settimanale, spesa, dispensa, food_cache,
ricerca full-text (FTS5).
"""
import aiosqlite
import json
import os
from datetime import date, timedelta

import econ_def

DB_PATH = os.environ.get("DB_PATH", "/config/haria.db")
CATEGORIA_TRASFERIMENTO = "trasferimento"  # esclusa dai report; protetta da rename/merge/delete
MAX_HISTORY = 10          # turni raw inviati a ogni richiesta
SUMMARY_BATCH = 20        # turni vecchi piegati nel summary per giro
_FTS_OK = False           # FTS5 disponibile (settato in init_db)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # WAL + busy_timeout: riduce 'database is locked' con accessi concorrenti
        # (webpanel + Telegram + scheduler + refresh MQTT). WAL e' persistente.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id TEXT PRIMARY KEY,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
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
            CREATE TABLE IF NOT EXISTS mqtt_topics (
                uid TEXT PRIMARY KEY,
                config_topic TEXT NOT NULL,
                state_topics TEXT NOT NULL,
                kind TEXT NOT NULL
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
            CREATE TABLE IF NOT EXISTS econ_conti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                tipo TEXT NOT NULL,
                intestatario TEXT NOT NULL DEFAULT 'famiglia',
                saldo_iniziale REAL NOT NULL DEFAULT 0,
                attivo INTEGER NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS econ_transazioni (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conto_id INTEGER NOT NULL REFERENCES econ_conti(id),
                data TEXT NOT NULL,
                importo REAL NOT NULL,
                categoria TEXT NOT NULL,
                descrizione TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_econ_transazioni_conto_id
                ON econ_transazioni(conto_id);
            CREATE TABLE IF NOT EXISTS econ_categorie (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS econ_budget (
                categoria TEXT PRIMARY KEY,
                importo REAL NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS econ_regole (
                keyword TEXT PRIMARY KEY,
                categoria TEXT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS econ_obiettivi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nome TEXT NOT NULL UNIQUE,
                target REAL NOT NULL,
                accantonato REAL NOT NULL DEFAULT 0,
                target_date TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # migrazione: aggiungi colonna intestatario se manca (DB pre-profilazione)
        cur = await db.execute("PRAGMA table_info(econ_conti)")
        ec_cols = [r[1] for r in await cur.fetchall()]
        if "intestatario" not in ec_cols:
            await db.execute(
                "ALTER TABLE econ_conti ADD COLUMN intestatario TEXT NOT NULL DEFAULT 'famiglia'"
            )
        # seed conti default da econ_def.CONTI (idempotente)
        for nome, d in econ_def.CONTI.items():
            await db.execute(
                "INSERT OR IGNORE INTO econ_conti (nome, tipo, intestatario) VALUES (?, ?, ?)",
                (nome, d["tipo"], d.get("intestatario", "famiglia")),
            )
        # migrazione profilazione (UNA-TANTUM): disattiva i conti generici
        # pre-profilazione (sostituiti dalle varianti per membro). Wrappata in
        # schema_migrations così non ri-disattiva un conto che l'utente potrebbe
        # ricreare con quel nome a un riavvio successivo.
        cur = await db.execute(
            "SELECT 1 FROM schema_migrations WHERE id='deactivate_generic_conti'"
        )
        if (await cur.fetchone()) is None:
            await db.execute(
                "UPDATE econ_conti SET attivo=0 WHERE nome IN ('postepay','paypal','contanti')"
            )
            await db.execute(
                "INSERT INTO schema_migrations (id) VALUES ('deactivate_generic_conti')"
            )
        # seed categorie default (idempotente)
        for cat in econ_def.CATEGORIE_DEFAULT:
            await db.execute(
                "INSERT OR IGNORE INTO econ_categorie (nome) VALUES (?)", (cat,)
            )
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
            ("econ_transazioni", "import_hash", "ALTER TABLE econ_transazioni ADD COLUMN import_hash TEXT"),
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
               ORDER BY id DESC LIMIT ?""",
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
            # rimuovi vecchie righe FTS della stessa nota (key) per evitare duplicati su update.
            # escape dei wildcard LIKE (%/_) nella key per non cancellare righe di altre note
            safe_key = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            await db.execute(
                "DELETE FROM memory_fts WHERE user_id = ? AND kind = 'note' "
                "AND content LIKE ? ESCAPE '\\'",
                (user_id, f"{safe_key}: %"),
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


async def get_mqtt_topics(kind: str) -> list[dict]:
    """Registro dei topic MQTT pubblicati per un kind (economia/bollette/food).
    Ritorna [{uid, config_topic, state_topics:[...]}]."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT uid, config_topic, state_topics FROM mqtt_topics WHERE kind=?", (kind,)
        )
        rows = await cur.fetchall()
    out = []
    for uid, ct, st in rows:
        try:
            topics = json.loads(st)
        except (ValueError, TypeError):
            topics = []
        out.append({"uid": uid, "config_topic": ct, "state_topics": topics})
    return out


async def set_mqtt_topics(kind: str, rows: list[dict]):
    """Sostituisce il registro dei topic per un kind con quelli del giro corrente."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM mqtt_topics WHERE kind=?", (kind,))
        for r in rows:
            await db.execute(
                "INSERT OR REPLACE INTO mqtt_topics (uid, config_topic, state_topics, kind) "
                "VALUES (?, ?, ?, ?)",
                (r["uid"], r["config_topic"],
                 json.dumps(r.get("state_topics", []), ensure_ascii=False), kind),
            )
        await db.commit()


