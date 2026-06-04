import aiosqlite
import os
from datetime import date, timedelta

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
        """)
        # migrazioni leggere: aggiungi colonne se mancano
        for table, col, ddl in [
            ("meal_plan", "kcal", "ALTER TABLE meal_plan ADD COLUMN kcal REAL"),
            ("shopping_items", "price", "ALTER TABLE shopping_items ADD COLUMN price REAL"),
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
                      COALESCE(SUM(carbs_g),0), COALESCE(SUM(fat_g),0), COUNT(*)
               FROM meals WHERE member = ? AND DATE(eaten_at) = ?""",
            (member, day),
        )
        r = await cursor.fetchone()
    return {"kcal": round(r[0], 1), "protein_g": round(r[1], 1),
            "carbs_g": round(r[2], 1), "fat_g": round(r[3], 1), "meals": r[4]}


# ---- food_diary: idratazione ----

async def add_hydration(member: str, ml: float) -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "INSERT INTO hydration_log (member, ml) VALUES (?, ?)", (member, ml)
        )
        await db.commit()
        hid = cursor.lastrowid
    return {"id": hid, "member": member, "ml": ml}


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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE shopping_items SET price = ? WHERE LOWER(name) LIKE LOWER(?)",
            (price, f"%{name}%"),
        )
        await db.commit()
        return cursor.rowcount > 0


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
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "UPDATE shopping_items SET checked = 1 WHERE checked = 0 AND LOWER(name) LIKE LOWER(?)",
            (f"%{name}%",),
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


async def clear_pantry() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("DELETE FROM pantry_items")
        await db.commit()
        return cursor.rowcount


# ---- food_diary: cache valori nutrizionali ----

async def get_food_cache(key: str) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            "SELECT data, source FROM food_cache WHERE key = ?", (key.lower(),)
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
