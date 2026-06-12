"""HARIA memory — bollette (split da memory.py; API invariata via facade)."""
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


async def set_bolletta(utility: str, metric: str, year: int, month: int, value: float):
    """Upsert singolo mese."""
    async with aiosqlite.connect(core.DB_PATH) as db:
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
    # l'ultimo mese assorbe il resto: la somma dei mesi == total (no perdita centesimi)
    last_val = round(float(total) - per * (n - 1), 1)
    async with aiosqlite.connect(core.DB_PATH) as db:
        for mth in range(m_start, m_end + 1):
            value = last_val if mth == m_end else per
            await db.execute(
                """INSERT INTO bollette (utility, metric, year, month, value, updated_at)
                   VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(utility, metric, year, month)
                   DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP""",
                (utility, metric, int(year), mth, value),
            )
        await db.commit()


async def get_bolletta_csv(utility: str, metric: str, year: int) -> str:
    """Ritorna i 12 valori mensili come CSV (0 per mesi mancanti)."""
    vals = [0.0] * 12
    async with aiosqlite.connect(core.DB_PATH) as db:
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
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT month, value FROM bollette
               WHERE utility=? AND metric=? AND year=? AND month>=? AND month<=? AND value!=0
               ORDER BY month""",
            (utility, metric, int(year), int(m_start), int(m_end)),
        )
        return [(r[0], r[1]) for r in await cursor.fetchall()]


async def get_bolletta_years(utility: str, metric: str) -> list[int]:
    """Anni con almeno un valore per (utility, metric)."""
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT DISTINCT year FROM bollette WHERE utility=? AND metric=? ORDER BY year",
            (utility, metric),
        )
        return [r[0] for r in await cursor.fetchall()]


async def bollette_is_empty() -> bool:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute("SELECT 1 FROM bollette LIMIT 1")
        return (await cursor.fetchone()) is None


async def seed_bollette(rows: list[tuple]):
    """Bulk insert iniziale. rows = [(utility, metric, year, month, value), ...]."""
    if not rows:
        return
    async with aiosqlite.connect(core.DB_PATH) as db:
        await db.executemany(
            """INSERT INTO bollette (utility, metric, year, month, value)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(utility, metric, year, month) DO UPDATE SET value=excluded.value""",
            rows,
        )
        await db.commit()


# ---- Economia domestica (conti, transazioni) ------------------------------

