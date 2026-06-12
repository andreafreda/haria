# HARIA — Bugfix round 2 (review 2026-06-12, su v0.1.95)

Secondo giro di review, fatto DOPO l'applicazione dei 20 task di `docs/bugfix-todo.md`
(tutti chiusi in v0.1.92) e delle feature regole/trasferimenti (v0.1.93-95).
Questo file contiene: 1 regressione già fixata da committare + 5 problemi nuovi
trovati nel codice delle feature recenti.

## Regole operative

- Un task alla volta, in ordine (TASK 21 per primo: è già pronto, va solo committato).
- Dopo ogni fix: `cd haria/app && python -m pytest tests/ -q` (baseline: 150 passed).
- Bump `version` in `haria/config.yaml` a ogni commit (convenzione repo: un bump per commit).
- Stile commit: `fix(<area>): <descrizione>` in italiano.
- A task completato: spunta checkbox + riga nel Changelog in fondo.
- Non rifare i task di `docs/bugfix-todo.md`: sono chiusi.

---

### TASK 21 — COMMIT della regressione respond+tool già fixata nel working tree
- [x] Stato: FATTO (fix già applicato, va verificato e committato)

**File:** `haria/app/claude_engine.py` (righe ~326-356). **Il fix è GIÀ nel working tree, non committato.**

**Contesto:** il fix del vecchio TASK 11 (v0.1.92) aveva introdotto una regressione:
quando il modello chiamava `respond` insieme ad altri tool nello stesso messaggio,
il codice azzerava `reply` e proseguiva il loop, ma il messaggio assistant rimandato
all'API conteneva il blocco `tool_use` di respond SENZA un `tool_result` corrispondente.
L'API Anthropic esige un tool_result per OGNI tool_use → errore 400
`invalid_request_error` → l'utente riceveva "Errore di comunicazione con l'AI. Riprova."

**Fix già applicato (verificare presenza):** in `chat()`, gli id dei blocchi respond
vengono raccolti in `respond_ids` e, quando il respond viene ignorato perché ci sono
altri tool, si aggiunge un tool_result per ciascun id:

```python
if reply is not None and tool_results:
    for rid in respond_ids:
        tool_results.append({
            "type": "tool_result",
            "tool_use_id": rid,
            "content": ("Risposta ignorata: esamina prima i risultati "
                        "degli altri tool, poi richiama respond."),
        })
    reply = None
```

**Cosa fare:**
1. `git diff haria/app/claude_engine.py` — verificare che il diff corrisponda a quanto sopra (respond_ids raccolti nel for, tool_result aggiunto per ogni id).
2. Test: `python -m pytest tests/ -q` → 150 passed.
3. Commit: `fix(engine): tool_result anche per respond ignorato (evita API 400)` + bump version.

---

### TASK 22 — `applica_regole` sovrascrive le correzioni manuali
- [x] Stato: FATTO

**File:** `haria/app/modules/economia.py`, `_gestisci_regole` ramo `aggiungi` (riga ~368-380); `haria/app/memory.py`, `applica_regole` (riga ~1755).

**Problema:** ogni `gestisci_regole azione=aggiungi` esegue `applica_regole()` su TUTTE
le transazioni con TUTTE le regole. Se l'utente aveva corretto a mano la categoria di
un movimento (es. spesa Esselunga spostata in "regali"), l'aggiunta di QUALSIASI regola
successiva gliela riporta ad "alimentari". Perdita silenziosa di lavoro manuale.

**Fix (due parti):**

Parte A — nel ramo `aggiungi` di `_gestisci_regole`, applicare SOLO la regola nuova,
non tutte. Aggiungere in `memory.py`:

```python
async def applica_regola(keyword: str) -> int:
    """Applica UNA regola alle transazioni esistenti. Ritorna n. aggiornate."""
    kw = (keyword or "").strip().lower()
    async with aiosqlite.connect(DB_PATH) as db:
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
```

e in `_gestisci_regole` ramo `aggiungi` sostituire `res = await applica_regole()` con
`n = await applica_regola(kw)` (adeguare il JSON di risposta: `"aggiornati_esistenti": n`).

Parte B — l'azione esplicita `applica` resta com'è (ri-applica tutto), MA la
descrizione del tool `gestisci_regole` deve avvisare: aggiungere alla description
"ATTENZIONE: 'applica' ri-applica TUTTE le regole a TUTTI i movimenti e sovrascrive
eventuali categorie corrette a mano; usala solo su richiesta esplicita."

**Verifica:** test in `tests/test_modules_economia.py`: transazione con descrizione
"esselunga" e categoria "regali" (correzione manuale) + regola esistente
`esselunga→alimentari`; aggiungere NUOVA regola `conad→alimentari` → la transazione
"esselunga" deve RESTARE in "regali".

---

### TASK 23 — Categoria 'trasferimento' hardcoded, rename la rompe
- [x] Stato: FATTO

**File:** `haria/app/memory.py` — `riepilogo_spese` (riga ~2171), `andamento_mensile` (riga ~2216), `spese_categoria_anno` (riga ~2243), `rename_categoria` (riga ~1880), `merge_categoria` (riga ~1911), `delete_categoria` (riga ~1857).

**Problema:** l'esclusione dei giroconti dai report usa il literal `t.categoria!='trasferimento'`
in 3 query. Se l'utente rinomina o unisce la categoria `trasferimento` via
`gestisci_categorie`, l'esclusione smette di funzionare in silenzio: i giroconti
ricompaiono come entrate/uscite nei report.

**Fix:** proteggere la categoria. In `memory.py` aggiungere in testa (vicino alle costanti):

```python
CATEGORIA_TRASFERIMENTO = "trasferimento"  # esclusa dai report; protetta da rename/merge/delete
```

1. Sostituire i 3 literal `'trasferimento'` nelle query con parametro bind `?` e `CATEGORIA_TRASFERIMENTO` nei params (attenzione all'ordine dei params: il filtro va come PRIMO parametro se la condizione è prima nel WHERE).
2. In `rename_categoria` e `merge_categoria`: se la sorgente (old/src, confronto case-insensitive) è `CATEGORIA_TRASFERIMENTO`, ritornare `False` senza toccare nulla.
3. In `delete_categoria`: se la categoria è `CATEGORIA_TRASFERIMENTO`, ritornare `{"ok": False, "protetta": True}`.
4. In `modules/economia.py`, `_gestisci_categorie`: gestire il caso `protetta` con messaggio chiaro ("La categoria 'trasferimento' è di sistema: serve a escludere i giroconti dai report, non si può rinominare/eliminare.").

**Verifica:** test: `rename_categoria("trasferimento", "giroconti")` → `False`; transazione categoria trasferimento → `riepilogo_spese()` continua a escluderla.

---

### TASK 24 — `add_regola` accetta keyword troppo corte
- [x] Stato: FATTO

**File:** `haria/app/memory.py`, `add_regola` (riga ~1716); `haria/app/modules/economia.py`, `_gestisci_regole` ramo `aggiungi`.

**Problema:** nessun controllo lunghezza. Una keyword di 1-2 caratteri (es. "a", "po")
matcha per substring quasi ogni descrizione: al prossimo `applica` (o al prossimo
import) ricategorizza in massa tutto il DB.

**Fix:** in `memory.add_regola`, dopo il check vuoto:

```python
if len(kw) < 3:
    raise ValueError("keyword troppo corta: minimo 3 caratteri")
```

In `_gestisci_regole` ramo `aggiungi`, intercettare:

```python
try:
    c = await add_regola(kw, cat)
except ValueError as e:
    return f"Regola non valida: {e}"
```

**Verifica:** test: `add_regola("po", "x")` → ValueError; tool `gestisci_regole aggiungi` con keyword "po" → messaggio errore, nessuna regola creata.

---

### TASK 25 — Messaggio 503 briefing stale
- [x] Stato: FATTO

**File:** `haria/app/webpanel.py`, `_h_briefings_save` (riga ~820).

**Problema:** il messaggio dice "abilita almeno un modulo tra agenda/food_diary/news",
ma da v0.1.92 lo scheduler parte anche con bollette/economia (main.py riga ~129).
Messaggio fuorviante.

**Fix:** cambiare il testo in:
`"Scheduler non attivo: abilita almeno un modulo tra agenda/food_diary/news/bollette/economia"`.

---

### TASK 26 — Regola può puntare a categoria cancellata
- [x] Stato: FATTO

**File:** `haria/app/memory.py`, `delete_categoria` (riga ~1857).

**Problema:** `delete_categoria` controlla solo le transazioni, non `econ_regole`.
Si può cancellare una categoria ancora referenziata da una regola: la regola orfana
ricrea la categoria al prossimo match (comportamento sorprendente) oppure inserisce
transazioni con categoria non in `econ_categorie`.

**Fix:** in `delete_categoria`, dopo il check transazioni, aggiungere:

```python
cur = await db.execute(
    "SELECT count(*) FROM econ_regole WHERE categoria=?", (canon,)
)
nr = (await cur.fetchone())[0]
if nr > 0:
    return {"ok": False, "in_uso_regole": True, "regole": nr}
```

In `modules/economia.py`, `_gestisci_categorie` ramo `elimina`: gestire `in_uso_regole`
con messaggio "Categoria usata da N regole di auto-categorizzazione: elimina prima le
regole (gestisci_regole)."

**Verifica:** test: regola `x→svago` + `delete_categoria("svago")` → rifiutato con `in_uso_regole`.

---

## Fuori scope (NON toccare senza ok di Andrea)

Identico alla lista in `docs/bugfix-todo.md`: split memory.py, entità MQTT fantasma,
pin requirements, filtro entity cache, max_tokens=1024, moduli config morti
(telegram/ha_chat/voice), auth webpanel.

## Changelog fix

(una riga per task completato: data, task, commit)

- 2026-06-12 — TASK 21 — fix(engine): tool_result anche per respond ignorato (evita API 400) — v0.1.96
- 2026-06-12 — TASK 22-26 — fix(economia): applica_regola singola, trasferimento protetta+param, keyword min 3, 503 msg, delete_categoria controlla regole — v0.1.97
