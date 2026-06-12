"""HARIA memory — economia: conti, transazioni, categorie, regole, budget, obiettivi, report
(split da memory.py; API invariata via facade memory/__init__.py)."""
import sys
from . import core
import aiosqlite
import json
from datetime import date, timedelta

import econ_def


def __getattr__(name):
    _c = sys.modules["memory.core"]
    if hasattr(_c, name):
        return getattr(_c, name)
    return getattr(sys.modules["memory"], name)


def _conto_dict(r) -> dict:
    return {"id": r[0], "nome": r[1], "tipo": r[2], "intestatario": r[3],
            "saldo_iniziale": r[4], "attivo": bool(r[5])}


async def list_conti(solo_attivi: bool = True) -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        sql = "SELECT id, nome, tipo, intestatario, saldo_iniziale, attivo FROM econ_conti"
        if solo_attivi:
            sql += " WHERE attivo=1"
        sql += " ORDER BY id"
        cursor = await db.execute(sql)
        rows = await cursor.fetchall()
    return [_conto_dict(r) for r in rows]


async def get_conto(nome: str) -> dict | None:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT id, nome, tipo, intestatario, saldo_iniziale, attivo "
            "FROM econ_conti WHERE nome=?",
            (nome,),
        )
        r = await cursor.fetchone()
    return _conto_dict(r) if r else None


async def add_conto(nome: str, tipo: str, saldo_iniziale: float = 0.0,
                    intestatario: str = "famiglia") -> int:
    """Crea (o riattiva) un conto custom, oltre a quelli seedati da econ_def."""
    async with aiosqlite.connect(core.DB_PATH) as db:
        await db.execute(
            """INSERT INTO econ_conti (nome, tipo, intestatario, saldo_iniziale, attivo)
               VALUES (?, ?, ?, ?, 1)
               ON CONFLICT(nome) DO UPDATE SET
                   tipo=excluded.tipo, intestatario=excluded.intestatario,
                   saldo_iniziale=excluded.saldo_iniziale, attivo=1""",
            (nome, tipo, intestatario, float(saldo_iniziale)),
        )
        await db.commit()
        cursor = await db.execute("SELECT id FROM econ_conti WHERE nome=?", (nome,))
        return (await cursor.fetchone())[0]


async def update_conto(nome: str, *, nuovo_nome: str | None = None,
                       tipo: str | None = None, intestatario: str | None = None,
                       saldo_iniziale: float | None = None,
                       attivo: bool | None = None) -> bool:
    """Aggiorna i campi forniti di un conto (gli altri restano invariati).
    False se il conto non esiste."""
    sets, params = [], []
    if nuovo_nome is not None:
        sets.append("nome=?"); params.append(nuovo_nome.strip())
    if tipo is not None:
        sets.append("tipo=?"); params.append(tipo)
    if intestatario is not None:
        sets.append("intestatario=?"); params.append(intestatario)
    if saldo_iniziale is not None:
        sets.append("saldo_iniziale=?"); params.append(float(saldo_iniziale))
    if attivo is not None:
        sets.append("attivo=?"); params.append(1 if attivo else 0)
    if not sets:
        return False
    params.append(nome)
    async with aiosqlite.connect(core.DB_PATH) as db:
        try:
            cur = await db.execute(
                f"UPDATE econ_conti SET {', '.join(sets)} WHERE nome=?", params
            )
        except aiosqlite.IntegrityError:
            # nuovo_nome collide con un conto esistente (UNIQUE)
            return False
        await db.commit()
        return cur.rowcount > 0


async def delete_conto(nome: str) -> dict:
    """Elimina un conto. Se ha transazioni NON cancella (ritorna in_uso=True):
    usare update_conto(attivo=False) per disattivarlo invece di perdere i dati."""
    conto_row = await get_conto(nome)
    if conto_row is None:
        return {"ok": False, "trovato": False}
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT count(*) FROM econ_transazioni WHERE conto_id=?", (conto_row["id"],)
        )
        n = (await cur.fetchone())[0]
        if n > 0:
            return {"ok": False, "in_uso": True, "transazioni": n}
        await db.execute("DELETE FROM econ_conti WHERE id=?", (conto_row["id"],))
        await db.commit()
    return {"ok": True}


async def add_transazione(conto: str, data: str, importo: float,
                           categoria: str, descrizione: str = "") -> int:
    """Registra movimento. importo firmato: + entrata, - uscita."""
    conto_row = await get_conto(conto)
    if conto_row is None:
        raise ValueError(f"conto sconosciuto: {conto}")
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            """INSERT INTO econ_transazioni (conto_id, data, importo, categoria, descrizione)
               VALUES (?, ?, ?, ?, ?)""",
            (conto_row["id"], data, float(importo), categoria, descrizione),
        )
        await db.commit()
        return cursor.lastrowid


async def get_saldo(conto: str) -> float:
    """Saldo = saldo_iniziale + somma transazioni."""
    conto_row = await get_conto(conto)
    if conto_row is None:
        raise ValueError(f"conto sconosciuto: {conto}")
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(importo), 0) FROM econ_transazioni WHERE conto_id=?",
            (conto_row["id"],),
        )
        somma = (await cursor.fetchone())[0]
    return round(conto_row["saldo_iniziale"] + somma, 2)


async def list_transazioni(conto: str | None = None, data_da: str | None = None,
                            data_a: str | None = None, categoria: str | None = None,
                            limit: int = 100) -> list[dict]:
    sql = """SELECT t.id, c.nome, t.data, t.importo, t.categoria, t.descrizione
             FROM econ_transazioni t JOIN econ_conti c ON c.id = t.conto_id
             WHERE 1=1"""
    params: list = []
    if conto:
        sql += " AND c.nome=?"
        params.append(conto)
    if data_da:
        sql += " AND t.data>=?"
        params.append(data_da)
    if data_a:
        sql += " AND t.data<=?"
        params.append(data_a)
    if categoria:
        sql += " AND t.categoria=?"
        params.append(categoria)
    sql += " ORDER BY t.data DESC, t.id DESC LIMIT ?"
    params.append(int(limit))
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
    return [
        {"id": r[0], "conto": r[1], "data": r[2], "importo": r[3], "categoria": r[4], "descrizione": r[5]}
        for r in rows
    ]


async def delete_transazione(transazione_id: int) -> bool:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute("DELETE FROM econ_transazioni WHERE id=?", (transazione_id,))
        await db.commit()
        return cursor.rowcount > 0


async def update_transazione(transazione_id: int, *, data: str | None = None,
                             importo: float | None = None, categoria: str | None = None,
                             descrizione: str | None = None,
                             conto: str | None = None) -> bool:
    """Aggiorna i campi forniti di una transazione. False se l'id non esiste
    o il conto indicato è sconosciuto."""
    sets, params = [], []
    if data is not None:
        sets.append("data=?"); params.append(data)
    if importo is not None:
        sets.append("importo=?"); params.append(float(importo))
    if categoria is not None:
        sets.append("categoria=?"); params.append(categoria)
    if descrizione is not None:
        sets.append("descrizione=?"); params.append(descrizione)
    if conto is not None:
        cr = await get_conto(conto)
        if cr is None:
            return False
        sets.append("conto_id=?"); params.append(cr["id"])
    if not sets:
        return False
    params.append(int(transazione_id))
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            f"UPDATE econ_transazioni SET {', '.join(sets)} WHERE id=?", params
        )
        await db.commit()
        return cur.rowcount > 0


async def add_regola(keyword: str, categoria: str) -> str:
    """Regola di categorizzazione: ogni movimento la cui descrizione contiene
    `keyword` (case-insensitive) viene messo in `categoria` all'import."""
    kw = (keyword or "").strip().lower()
    cat = (categoria or "").strip().lower()
    if not kw or not cat:
        raise ValueError("keyword e categoria obbligatorie")
    if len(kw) < 3:
        raise ValueError("keyword troppo corta: minimo 3 caratteri")
    async with aiosqlite.connect(core.DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO econ_categorie (nome) VALUES (?)", (cat,))
        await db.execute(
            "INSERT INTO econ_regole (keyword, categoria) VALUES (?, ?) "
            "ON CONFLICT(keyword) DO UPDATE SET categoria=excluded.categoria",
            (kw, cat),
        )
        await db.commit()
    return cat


async def delete_regola(keyword: str) -> bool:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM econ_regole WHERE keyword=?", ((keyword or "").strip().lower(),)
        )
        await db.commit()
        return cur.rowcount > 0


async def list_regole() -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT keyword, categoria FROM econ_regole ORDER BY length(keyword) DESC"
        )
        return [{"keyword": r[0], "categoria": r[1]} for r in await cur.fetchall()]


def _match_regola(descr: str, regole: list[dict]) -> str | None:
    """Categoria della prima regola (keyword più lunga) contenuta in descr."""
    d = (descr or "").lower()
    for r in regole:  # già ordinate per keyword più lunga (più specifica)
        if r["keyword"] in d:
            return r["categoria"]
    return None


async def applica_regole() -> dict:
    """Ri-applica le regole a TUTTE le transazioni esistenti. Ritorna conteggio
    aggiornamenti per categoria."""
    regole = await list_regole()
    if not regole:
        return {}
    import collections
    out = collections.Counter()
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute("SELECT id, descrizione, categoria FROM econ_transazioni")
        rows = await cur.fetchall()
        for tid, descr, cat in rows:
            nuova = _match_regola(descr, regole)
            if nuova and nuova != cat:
                await db.execute("UPDATE econ_transazioni SET categoria=? WHERE id=?", (nuova, tid))
                out[nuova] += 1
        await db.commit()
    return dict(out)


async def applica_regola(keyword: str) -> int:
    """Applica UNA regola alle transazioni esistenti. Ritorna n. aggiornate.
    Usata all'aggiunta di una regola: non tocca le altre categorie già corrette
    a mano (a differenza di applica_regole che ri-applica tutto)."""
    kw = (keyword or "").strip().lower()
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT categoria FROM econ_regole WHERE keyword=?", (kw,))
        row = await cur.fetchone()
        if not row:
            return 0
        cat = row[0]
        cur = await db.execute(
            "UPDATE econ_transazioni SET categoria=? "
            "WHERE categoria!=? AND instr(lower(descrizione), ?) > 0",
            (cat, cat, kw),
        )
        await db.commit()
        return cur.rowcount


async def import_transazioni(conto: str, movimenti: list[dict]) -> dict:
    """Bulk import movimenti da estratto. Ogni movimento: {data, importo,
    descrizione, categoria, hash}. Dedup via import_hash (per conto): reimportare
    lo stesso file non duplica. Ritorna {inserite, duplicate, totale}."""
    conto_row = await get_conto(conto)
    if conto_row is None:
        raise ValueError(f"conto sconosciuto: {conto}")
    cid = conto_row["id"]
    regole = await list_regole()  # override categoria su keyword descrizione
    inserite = duplicate = 0
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT import_hash FROM econ_transazioni "
            "WHERE conto_id=? AND import_hash IS NOT NULL",
            (cid,),
        )
        existing = {r[0] for r in await cur.fetchall()}
        for m in movimenti:
            h = m.get("hash")
            if h and h in existing:
                duplicate += 1
                continue
            if h:
                existing.add(h)
            # regola utente ha priorità sulla categoria dedotta dal parser
            cat = (_match_regola(m.get("descrizione"), regole)
                   or (m.get("categoria") or "altro").strip().lower())
            await db.execute(
                "INSERT OR IGNORE INTO econ_categorie (nome) VALUES (?)", (cat,)
            )
            await db.execute(
                """INSERT INTO econ_transazioni
                   (conto_id, data, importo, categoria, descrizione, import_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (cid, m["data"], float(m["importo"]), cat,
                 (m.get("descrizione") or "").strip(), h),
            )
            inserite += 1
        await db.commit()
    return {"inserite": inserite, "duplicate": duplicate, "totale": len(movimenti)}


async def list_categorie() -> list[str]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute("SELECT nome FROM econ_categorie ORDER BY nome")
        return [r[0] for r in await cursor.fetchall()]


async def normalize_categoria(nome: str) -> str:
    """Ritorna la categoria canonica. Match case-insensitive su quelle esistenti;
    se nuova, la registra (forma trimmed/lowercase) e la ritorna."""
    raw = (nome or "").strip()
    if not raw:
        raw = "varie"
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT nome FROM econ_categorie WHERE lower(nome)=lower(?)", (raw,)
        )
        row = await cursor.fetchone()
        if row:
            return row[0]
        canon = raw.lower()
        await db.execute(
            "INSERT OR IGNORE INTO econ_categorie (nome) VALUES (?)", (canon,)
        )
        await db.commit()
        # re-select: ritorna la forma effettivamente memorizzata (robusto se una
        # variante e' stata creata in mezzo)
        cursor = await db.execute(
            "SELECT nome FROM econ_categorie WHERE lower(nome)=lower(?)", (canon,)
        )
        row = await cursor.fetchone()
        return row[0] if row else canon


async def delete_categoria(nome: str) -> dict:
    """Elimina una categoria. Se è usata da transazioni NON cancella
    (ritorna in_uso + conteggio): usare rename/merge per spostarle prima."""
    cat = (nome or "").strip()
    if cat.lower() == core.CATEGORIA_TRASFERIMENTO:
        return {"ok": False, "protetta": True}
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT nome FROM econ_categorie WHERE lower(nome)=lower(?)", (cat,)
        )
        row = await cur.fetchone()
        if not row:
            return {"ok": False, "trovato": False}
        canon = row[0]
        cur = await db.execute(
            "SELECT count(*) FROM econ_transazioni WHERE categoria=?", (canon,)
        )
        n = (await cur.fetchone())[0]
        if n > 0:
            return {"ok": False, "in_uso": True, "transazioni": n}
        cur = await db.execute(
            "SELECT count(*) FROM econ_regole WHERE categoria=?", (canon,)
        )
        nr = (await cur.fetchone())[0]
        if nr > 0:
            return {"ok": False, "in_uso_regole": True, "regole": nr}
        await db.execute("DELETE FROM econ_categorie WHERE nome=?", (canon,))
        await db.commit()
    return {"ok": True}


async def rename_categoria(old: str, new: str) -> bool:
    """Rinomina categoria + propaga su tutte le transazioni. False se 'old' assente."""
    new = (new or "").strip().lower()
    if not new:
        return False
    if (old or "").strip().lower() == core.CATEGORIA_TRASFERIMENTO:
        return False  # categoria di sistema, esclude i giroconti dai report
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT nome FROM econ_categorie WHERE lower(nome)=lower(?)", (old,)
        )
        row = await cursor.fetchone()
        if not row:
            return False
        old_canon = row[0]
        # se 'new' esiste gia' -> equivale a un merge
        cursor = await db.execute(
            "SELECT nome FROM econ_categorie WHERE lower(nome)=lower(?)", (new,)
        )
        exists = await cursor.fetchone()
        await db.execute(
            "UPDATE econ_transazioni SET categoria=? WHERE categoria=?", (new, old_canon)
        )
        await db.execute(
            "UPDATE econ_regole SET categoria=? WHERE categoria=?", (new, old_canon)
        )
        if exists:
            await db.execute("DELETE FROM econ_categorie WHERE nome=?", (old_canon,))
        else:
            await db.execute(
                "UPDATE econ_categorie SET nome=? WHERE nome=?", (new, old_canon)
            )
        await db.commit()
        return True


async def merge_categoria(src: str, dst: str) -> bool:
    """Sposta tutte le transazioni da 'src' a 'dst' e cancella 'src'.
    False se src assente. 'dst' viene creata se non esiste."""
    if (src or "").strip().lower() == core.CATEGORIA_TRASFERIMENTO:
        return False  # categoria di sistema, esclude i giroconti dai report
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT nome FROM econ_categorie WHERE lower(nome)=lower(?)", (src,)
        )
        srow = await cursor.fetchone()
        if not srow:
            return False
        src_canon = srow[0]
        dst_canon = (dst or "").strip().lower()
        if not dst_canon or dst_canon == src_canon.lower():
            return False
        await db.execute(
            "INSERT OR IGNORE INTO econ_categorie (nome) VALUES (?)", (dst_canon,)
        )
        # usa la forma canonica gia' registrata per dst
        cursor = await db.execute(
            "SELECT nome FROM econ_categorie WHERE lower(nome)=lower(?)", (dst_canon,)
        )
        dst_canon = (await cursor.fetchone())[0]
        await db.execute(
            "UPDATE econ_transazioni SET categoria=? WHERE categoria=?", (dst_canon, src_canon)
        )
        await db.execute(
            "UPDATE econ_regole SET categoria=? WHERE categoria=?", (dst_canon, src_canon)
        )
        await db.execute("DELETE FROM econ_categorie WHERE nome=?", (src_canon,))
        await db.commit()
        return True


async def set_budget(categoria: str, importo: float) -> str:
    """Imposta (upsert) il budget mensile per una categoria. importo memorizzato
    in valore assoluto. La rimozione si fa con delete_budget (non con importo 0 qui).
    Ritorna la categoria canonica usata."""
    cat = await normalize_categoria(categoria)
    async with aiosqlite.connect(core.DB_PATH) as db:
        await db.execute(
            """INSERT INTO econ_budget (categoria, importo, updated_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(categoria) DO UPDATE SET
                   importo=excluded.importo, updated_at=CURRENT_TIMESTAMP""",
            (cat, abs(float(importo))),
        )
        await db.commit()
    return cat


async def delete_budget(categoria: str) -> bool:
    cat = (categoria or "").strip().lower()
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "DELETE FROM econ_budget WHERE lower(categoria)=lower(?)", (cat,)
        )
        await db.commit()
        return cursor.rowcount > 0


async def list_budget() -> list[dict]:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            "SELECT categoria, importo FROM econ_budget ORDER BY categoria"
        )
        return [{"categoria": r[0], "importo": r[1]} for r in await cursor.fetchall()]


async def get_budget_status(year: int, month: int) -> list[dict]:
    """Per ogni categoria con budget: speso (uscite del mese), budget, residuo, perc.
    Ordinato per perc decrescente (sforamenti prima)."""
    ym = f"{int(year):04d}-{int(month):02d}-%"
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT b.categoria, b.importo,
                      COALESCE(-SUM(CASE WHEN t.importo < 0 THEN t.importo END), 0)
               FROM econ_budget b
               LEFT JOIN econ_transazioni t
                 ON t.categoria = b.categoria AND t.data LIKE ?
               GROUP BY b.categoria, b.importo""",
            (ym,),
        )
        rows = await cursor.fetchall()
    out = []
    for cat, budget, speso in rows:
        budget = float(budget)
        speso = round(float(speso), 2)
        residuo = round(budget - speso, 2)
        perc = round(speso / budget * 100, 1) if budget else 0.0
        out.append({
            "categoria": cat,
            "budget": budget,
            "speso": speso,
            "residuo": residuo,
            "perc": perc,
            "sforato": speso > budget,
        })
    out.sort(key=lambda x: x["perc"], reverse=True)
    return out


def _mesi_rimanenti(target_date: str, today: date) -> int:
    """Mesi interi rimanenti fino a target_date. 0 se scaduto/oggi."""
    try:
        d = date.fromisoformat(target_date)
    except (ValueError, TypeError):
        return 0
    if d <= today:
        return 0
    months = (d.year - today.year) * 12 + (d.month - today.month)
    if d.day < today.day:
        months -= 1
    return max(months, 1)


async def set_obiettivo(nome: str, target: float, target_date: str | None = None) -> int:
    """Crea o aggiorna (target/scadenza) un obiettivo di risparmio. Non tocca
    l'accantonato esistente. Ritorna l'id."""
    nome = (nome or "").strip()
    async with aiosqlite.connect(core.DB_PATH) as db:
        await db.execute(
            """INSERT INTO econ_obiettivi (nome, target, target_date)
               VALUES (?, ?, ?)
               ON CONFLICT(nome) DO UPDATE SET
                   target=excluded.target, target_date=excluded.target_date""",
            (nome, abs(float(target)), target_date or None),
        )
        await db.commit()
        cur = await db.execute("SELECT id FROM econ_obiettivi WHERE nome=?", (nome,))
        return (await cur.fetchone())[0]


async def accantona(nome: str, importo: float) -> dict | None:
    """Aggiunge (o sottrae se importo<0) all'accantonato di un obiettivo.
    accantonato non scende sotto 0. None se l'obiettivo non esiste."""
    async with aiosqlite.connect(core.DB_PATH) as db:
        # update atomico SQL-side (no read-modify-write), floored a 0
        cur = await db.execute(
            """UPDATE econ_obiettivi
               SET accantonato = max(0, round(accantonato + ?, 2))
               WHERE lower(nome)=lower(?)""",
            (float(importo), (nome or "").strip()),
        )
        if cur.rowcount == 0:
            return None
        await db.commit()
        cur = await db.execute(
            "SELECT nome, accantonato, target FROM econ_obiettivi WHERE lower(nome)=lower(?)",
            ((nome or "").strip(),),
        )
        nome_db, nuovo, target = await cur.fetchone()
    return {"nome": nome_db, "accantonato": nuovo, "target": target,
            "raggiunto": nuovo >= target}


async def delete_obiettivo(nome: str) -> bool:
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "DELETE FROM econ_obiettivi WHERE lower(nome)=lower(?)", ((nome or "").strip(),)
        )
        await db.commit()
        return cur.rowcount > 0


async def get_obiettivi() -> list[dict]:
    """Obiettivi con campi calcolati: residuo, perc, mesi_rimanenti,
    quota_mensile suggerita = residuo / mesi_rimanenti."""
    today = date.today()
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT nome, target, accantonato, target_date FROM econ_obiettivi ORDER BY nome"
        )
        rows = await cur.fetchall()
    out = []
    for nome, target, acc, tdate in rows:
        target = float(target)
        acc = round(float(acc), 2)
        residuo = round(max(target - acc, 0.0), 2)
        perc = round(acc / target * 100, 1) if target else 0.0
        mesi = _mesi_rimanenti(tdate, today) if tdate else None
        if residuo <= 0:
            quota = 0.0
        elif mesi is None:
            quota = None
        elif mesi <= 0:
            quota = residuo  # scaduto: serve tutto subito
        else:
            quota = round(residuo / mesi, 2)
        out.append({
            "nome": nome, "target": target, "accantonato": acc,
            "residuo": residuo, "perc": perc, "target_date": tdate,
            "mesi_rimanenti": mesi, "quota_mensile": quota,
            "raggiunto": acc >= target,
        })
    return out


async def reset_economia(reset_categorie: bool = False,
                         reset_saldi: bool = False) -> dict:
    """Reset dati economia (fine test). Svuota sempre transazioni e budget.
    Opzionale: ripristina categorie al seed, azzera i saldi iniziali.
    Ritorna il conteggio di cio' che e' stato cancellato."""
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute("SELECT count(*) FROM econ_transazioni")
        n_tx = (await cur.fetchone())[0]
        await db.execute("DELETE FROM econ_transazioni")
        cur = await db.execute("SELECT count(*) FROM econ_budget")
        n_budget = (await cur.fetchone())[0]
        await db.execute("DELETE FROM econ_budget")
        cur = await db.execute("SELECT count(*) FROM econ_obiettivi")
        n_obiettivi = (await cur.fetchone())[0]
        await db.execute("DELETE FROM econ_obiettivi")
        n_cat = 0
        if reset_categorie:
            cur = await db.execute("SELECT count(*) FROM econ_categorie")
            n_cat = (await cur.fetchone())[0]
            await db.execute("DELETE FROM econ_categorie")
            for cat in econ_def.CATEGORIE_DEFAULT:
                await db.execute(
                    "INSERT OR IGNORE INTO econ_categorie (nome) VALUES (?)", (cat,)
                )
        n_saldi = 0
        if reset_saldi:
            cur = await db.execute(
                "SELECT count(*) FROM econ_conti WHERE saldo_iniziale != 0"
            )
            n_saldi = (await cur.fetchone())[0]
            await db.execute("UPDATE econ_conti SET saldo_iniziale = 0")
        await db.commit()
    return {"transazioni_cancellate": n_tx,
            "budget_cancellati": n_budget,
            "obiettivi_cancellati": n_obiettivi,
            "categorie_resettate": n_cat,
            "saldi_azzerati": n_saldi}


async def get_saldi() -> list[dict]:
    """Saldo di tutti i conti attivi: [{conto, tipo, intestatario, saldo}]."""
    async with aiosqlite.connect(core.DB_PATH) as db:
        cursor = await db.execute(
            """SELECT c.nome, c.tipo, c.intestatario,
                      c.saldo_iniziale + COALESCE(SUM(t.importo), 0)
               FROM econ_conti c
               LEFT JOIN econ_transazioni t ON t.conto_id = c.id
               WHERE c.attivo = 1
               GROUP BY c.id
               ORDER BY c.id"""
        )
        rows = await cursor.fetchall()
    return [{"conto": r[0], "tipo": r[1], "intestatario": r[2], "saldo": round(r[3], 2)}
            for r in rows]


async def saldi_per_intestatario() -> dict:
    """Saldo totale aggregato per intestatario: {intestatario: saldo}."""
    out: dict = {}
    for s in await get_saldi():
        out[s["intestatario"]] = round(out.get(s["intestatario"], 0.0) + s["saldo"], 2)
    return out


async def riepilogo_spese(data_da: str | None = None, data_a: str | None = None,
                          conto: str | None = None, intestatario: str | None = None) -> dict:
    """Aggrega movimenti nel periodo: totale entrate/uscite/netto + breakdown
    spese per categoria (solo importi negativi). Filtri opz: conto, intestatario."""
    # esclude i trasferimenti INTERNI tra i propri conti (ricariche, P2P famiglia):
    # non sono né reddito né spesa reale. I bonifici a terzi NON sono 'trasferimento'
    # quindi restano contati. I saldi (get_saldi) invece li contano sempre.
    where = "WHERE t.categoria!=?"
    params: list = [core.CATEGORIA_TRASFERIMENTO]
    if conto:
        where += " AND c.nome=?"
        params.append(conto)
    if intestatario:
        where += " AND c.intestatario=?"
        params.append(intestatario)
    if data_da:
        where += " AND t.data>=?"
        params.append(data_da)
    if data_a:
        where += " AND t.data<=?"
        params.append(data_a)
    base = f"FROM econ_transazioni t JOIN econ_conti c ON c.id = t.conto_id {where}"
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            f"""SELECT
                  COALESCE(SUM(CASE WHEN t.importo > 0 THEN t.importo END), 0),
                  COALESCE(SUM(CASE WHEN t.importo < 0 THEN t.importo END), 0)
                {base}""",
            params,
        )
        entrate, uscite = await cur.fetchone()
        entrate, uscite = float(entrate), float(uscite)
        cur = await db.execute(
            f"""SELECT t.categoria, SUM(t.importo) tot
                {base} AND t.importo < 0
                GROUP BY t.categoria ORDER BY tot ASC""",
            params,
        )
        cats = [{"categoria": r[0], "totale": round(r[1], 2)} for r in await cur.fetchall()]
    return {
        "entrate": round(entrate, 2),
        "uscite": round(uscite, 2),
        "netto": round(entrate + uscite, 2),
        "per_categoria": cats,
    }


async def andamento_mensile(year: int, intestatario: str | None = None) -> list[dict]:
    """Per i 12 mesi dell'anno: entrate, uscite, netto. Filtro opz intestatario."""
    where = "WHERE t.data LIKE ? AND t.categoria!=?"
    params: list = [f"{int(year):04d}-%", core.CATEGORIA_TRASFERIMENTO]
    if intestatario:
        where += " AND c.intestatario=?"
        params.append(intestatario)
    out = [{"mese": m, "entrate": 0.0, "uscite": 0.0, "netto": 0.0} for m in range(1, 13)]
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            f"""SELECT CAST(substr(t.data, 6, 2) AS INTEGER) m,
                       COALESCE(SUM(CASE WHEN t.importo > 0 THEN t.importo END), 0),
                       COALESCE(SUM(CASE WHEN t.importo < 0 THEN -t.importo END), 0)
                FROM econ_transazioni t JOIN econ_conti c ON c.id = t.conto_id
                {where}
                GROUP BY m""",
            params,
        )
        for m, entr, usc in await cur.fetchall():
            if 1 <= m <= 12:
                out[m - 1]["entrate"] = round(float(entr), 2)
                out[m - 1]["uscite"] = round(float(usc), 2)
                out[m - 1]["netto"] = round(float(entr) - float(usc), 2)
    return out


async def spese_categoria_anno(year: int, intestatario: str | None = None) -> list[dict]:
    """Spese (uscite) per categoria nell'anno: [{categoria, totale, mesi:[12]}],
    ordinato per totale decrescente. Filtro opz intestatario."""
    where = "WHERE t.importo < 0 AND t.data LIKE ? AND t.categoria!=?"
    params: list = [f"{int(year):04d}-%", core.CATEGORIA_TRASFERIMENTO]
    if intestatario:
        where += " AND c.intestatario=?"
        params.append(intestatario)
    agg: dict = {}
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            f"""SELECT t.categoria, CAST(substr(t.data, 6, 2) AS INTEGER) m,
                       SUM(-t.importo)
                FROM econ_transazioni t JOIN econ_conti c ON c.id = t.conto_id
                {where}
                GROUP BY t.categoria, m""",
            params,
        )
        for cat, m, tot in await cur.fetchall():
            d = agg.setdefault(cat, {"categoria": cat, "totale": 0.0, "mesi": [0.0] * 12})
            if 1 <= m <= 12:
                d["mesi"][m - 1] = round(float(tot), 2)
                d["totale"] = round(d["totale"] + float(tot), 2)
    return sorted(agg.values(), key=lambda x: x["totale"], reverse=True)


async def anni_con_dati() -> list[int]:
    """Anni distinti con transazioni (per la navigazione storica)."""
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT DISTINCT CAST(substr(data, 1, 4) AS INTEGER) y "
            "FROM econ_transazioni ORDER BY y"
        )
        return [r[0] for r in await cur.fetchall()]


async def spese_mensili_per_categoria(months: int | None = None) -> dict:
    """Matrice spese (uscite) per categoria × mesi. Esclude i trasferimenti interni.
    months=None (default) -> TUTTA la storia (dal primo movimento al mese corrente),
    così aggiungendo mesi non si perdono i precedenti. months=N -> solo ultimi N mesi.
    Ritorna {"mesi": ["YYYY-MM", ...], "categorie": {cat: [val...]}, "totali": [...]}.
    Un solo sensore con l'intera matrice per la dashboard Lovelace."""
    today = date.today()
    if months is None:
        async with aiosqlite.connect(core.DB_PATH) as db:
            cur = await db.execute("SELECT MIN(substr(data,1,7)) FROM econ_transazioni")
            first = (await cur.fetchone())[0]
        if first:
            fy, fm = int(first[:4]), int(first[5:7])
            months = (today.year - fy) * 12 + (today.month - fm) + 1
        else:
            months = 1
        months = max(1, min(months, 120))  # cap di sicurezza (10 anni)
    mesi = []
    y, m = today.year, today.month
    for _ in range(months):
        mesi.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    mesi.reverse()
    idx = {ym: i for i, ym in enumerate(mesi)}
    cats: dict = {}
    totali = [0.0] * len(mesi)
    async with aiosqlite.connect(core.DB_PATH) as db:
        cur = await db.execute(
            "SELECT substr(data,1,7) ym, categoria, SUM(-importo) "
            "FROM econ_transazioni "
            "WHERE importo<0 AND categoria!=? AND substr(data,1,7)>=? "
            "GROUP BY ym, categoria",
            (core.CATEGORIA_TRASFERIMENTO, mesi[0]),
        )
        for ym, cat, tot in await cur.fetchall():
            if ym not in idx:
                continue
            row = cats.setdefault(cat, [0.0] * len(mesi))
            row[idx[ym]] = round(float(tot), 2)
            totali[idx[ym]] = round(totali[idx[ym]] + float(tot), 2)
    # ordina categorie per totale complessivo decrescente
    cats = dict(sorted(cats.items(), key=lambda kv: -sum(kv[1])))
    return {"mesi": mesi, "categorie": cats, "totali": totali}
