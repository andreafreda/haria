# HARIA — Bugfix round 3 (review 2026-06-12, su v0.1.97)

Terzo giro di review. Focus su aree non coperte dai round precedenti: scheduler/agenda,
motore conversazionale, errorlog, edge-case cron. Round 1 (`bugfix-todo.md`, 20 task)
e round 2 (`bugfix-round2.md`, TASK 21-26) sono CHIUSI: non rifarli.

## Regole operative

- Un task alla volta, in ordine di priorità (TASK 27 per primo: può brickare l'addon).
- Dopo ogni fix: `cd haria/app && python -m pytest tests/ -q` (baseline: 157 passed).
- Bump `version` in `haria/config.yaml` a ogni commit.
- Stile commit: `fix(<area>): <descrizione>` in italiano.
- A task completato: spunta checkbox + riga nel Changelog in fondo.

---

## 🔴 PRIORITÀ 1

### TASK 27 — Reminder corrotto bricka l'avvio dell'addon
- [x] Stato: FATTO

**File:** `haria/app/scheduler.py` (`_schedule_one` riga ~79, `start` riga ~90) e `haria/app/modules/agenda.py` (`_h_reminders`, rami `set_reminder` e `update_reminder`).

**Problema (catena completa):**
1. `agenda.set_reminder` NON valida `remind_at`: lo passa raw a `add_reminder` (DB).
2. `scheduler._schedule_one` ramo one-shot fa `datetime.fromisoformat(r["remind_at"])`
   SENZA try: stringa non-ISO (es. il modello passa "domani alle 18") → `ValueError`;
   `remind_at=None` con `recurring=None` (ottenibile via `update_reminder` che azzera
   recurring) → `TypeError`.
3. Al tool call l'eccezione viene assorbita dal catch generico di `_run_tool`, MA il
   reminder resta ATTIVO nel DB.
4. Al riavvio successivo `scheduler.start()` itera i reminder attivi senza try →
   l'eccezione esplode → `main()` muore → **boot loop dell'addon** finché non si
   cancella a mano la riga dal DB.

**Fix (tre livelli, applicarli tutti):**

A) Validazione all'ingresso — in `agenda._h_reminders`, ramo `set_reminder`, prima di `add_reminder`:

```python
if remind_at:
    from datetime import datetime as _dt
    try:
        _dt.fromisoformat(remind_at)
    except (ValueError, TypeError):
        return "Errore: remind_at non è un datetime ISO valido (YYYY-MM-DDTHH:MM:SS)."
```

Stessa validazione nel ramo `update_reminder` se `inputs.get("remind_at")` è valorizzato.

B) `_schedule_one` robusto — wrappare il ramo one-shot:

```python
    else:
        try:
            when = datetime.fromisoformat(r["remind_at"])
        except (ValueError, TypeError) as e:
            logger.warning("remind_at non valido per promemoria %s: %s", rid, e)
            return False
        if when <= datetime.now():
            return False
        trigger = DateTrigger(run_date=when)
```

C) `start()` indistruttibile — un reminder marcio non deve fermare gli altri:

```python
    for r in await get_active_reminders():
        try:
            if _schedule_one(r):
                loaded += 1
        except Exception as e:
            logger.error("Promemoria %s non schedulabile (ignorato): %s", r.get("id"), e)
```

(stessa protezione in `load_briefings` per simmetria, anche se lì il cron è già in try).

**Verifica:** test in `tests/`: riga reminders con `remind_at="domani"`, `recurring=None` →
`_schedule_one` ritorna False senza eccezione. Tool `set_reminder` con
`remind_at="domani alle 18"` → messaggio errore, nessuna riga attiva in DB.

---

## 🟠 PRIORITÀ 2

### TASK 28 — HA irraggiungibile + cache entità vuota = silenzio totale all'utente
- [x] Stato: FATTO

**File:** `haria/app/claude_engine.py`, `_build_system` (riga ~212) e `chat()` (riga ~309).

**Problema:** `system = await _build_system(...)` sta FUORI dal try di `chat()`. Dentro
`_build_system`, se la cache entità è vuota viene chiamato `refresh_entity_cache()` →
`get_states` → con HA giù solleva `ConnectionError`. Non gestita: risale fino al
dispatcher PTB, l'utente non riceve NESSUNA risposta (nemmeno un errore). Scenario
tipico: primo avvio o dopo `/updateentities` fallito, con HA in riavvio.

**Fix:** rendere best-effort il blocco entità in `_build_system`:

```python
    cached = await get_entity_cache()
    if not cached:
        try:
            await refresh_entity_cache()
            cached = await get_entity_cache()
        except Exception as e:
            logger.warning("Entity cache non disponibile (HA giù?): %s", e)
            cached = None
    if cached:
        base += f"\n\nENTITÀ DISPONIBILI (entity_id | nome):\n{cached}"
    else:
        base += "\n\n(Entità HA non disponibili al momento: Home Assistant non risponde.)"
```

Così la chat funziona anche con HA giù (i tool HA falliranno con errore leggibile, già gestito da `_run_tool`).

**Verifica:** test con `get_states` mockata a sollevare `ConnectionError` e cache vuota →
`_build_system` ritorna senza eccezione e il system contiene la nota di indisponibilità.

---

### TASK 29 — `_conv_dow` rompe i cron con step nel campo giorno-settimana
- [x] Stato: FATTO

**File:** `haria/app/scheduler.py`, `_conv_dow` (riga ~31).

**Problema:** `re.sub(r"\d+", ...)` converte OGNI numero nel campo dow, inclusi gli
step: `*/2` diventa `*/tue` → `CronTrigger` solleva ValueError → un cron unix valido
("0 8 * * */2") viene rifiutato come "Cron non valido".

**Fix:** non toccare i numeri che seguono uno slash. Sostituire la regex con una che
converte solo cifre NON precedute da `/`:

```python
def _conv_dow(field: str) -> str:
    """Converte i numeri dow Unix in nomi APScheduler (gestisce *, liste, range).
    I numeri dopo '/' sono step e restano numerici (es. */2)."""
    if field == "*":
        return "*"
    return re.sub(r"(?<![/\d])\d+", lambda m: _DOW.get(m.group(0), m.group(0)), field)
```

NB il lookbehind `(?<![/\d])`: esclude sia i numeri dopo slash sia le seconde cifre di
numeri multi-cifra (che comunque non esistono in dow 0-7, difesa in più).

**Verifica:** test unit su `_conv_dow`: `"*/2"` → `"*/2"`; `"1-5"` → `"mon-fri"`;
`"0,6"` → `"sun,sat"`; `"5"` → `"fri"`. E `_cron_trigger("0 8 * * */2")` non solleva.

---

## 🟡 PRIORITÀ 3

### TASK 30 — Notifiche push errori senza throttle (spam sul telefono)
- [x] Stato: FATTO

**File:** `haria/app/errorlog.py`.

**Problema:** ogni `logger.error` di qualsiasi modulo (tranne errorlog/ha_client) manda
una notifica push via HA. Un errore ripetuto (es. retry promemoria: 2 error-log per giro,
briefing che fallisce ogni mattina, API Anthropic giù durante una conversazione) =
raffica di notifiche identiche sul telefono.

**Fix:** throttle per messaggio: max 1 notifica per (source, message) ogni 10 minuti.
Il log su DB resta SEMPRE (serve al pannello /logs); si throttla solo la push:

```python
import time
_last_notify: dict[tuple, float] = {}
_THROTTLE_S = 600

async def _dispatch(source: str, message: str, tb: str):
    try:
        await add_error_log(source, "ERROR", message, tb)
    except Exception:
        pass
    key = (source, message[:120])
    now = time.monotonic()
    if now - _last_notify.get(key, 0) < _THROTTLE_S:
        return
    _last_notify[key] = now
    # pulizia best-effort della mappa (evita crescita illimitata)
    if len(_last_notify) > 200:
        cutoff = now - _THROTTLE_S
        for k in [k for k, t in _last_notify.items() if t < cutoff]:
            del _last_notify[k]
    try:
        dom, svc = _notify_target()
        short = message if len(message) <= 200 else message[:200] + "…"
        await call_service(dom, svc, {"title": f"HARIA errore: {source}", "message": short})
    except Exception:
        pass
```

**Verifica:** test: due `_dispatch` identici ravvicinati → `call_service` (mockata) chiamata una sola volta; `add_error_log` due volte.

---

### TASK 31 — Escape wildcard nel delete FTS di `save_note`
- [x] Stato: FATTO

**File:** `haria/app/memory.py`, `save_note` (riga ~487 circa, il `DELETE FROM memory_fts ... LIKE`).

**Problema:** la pulizia delle righe FTS della nota usa `LIKE '{key}: %'`. Una key che
contiene `%` o `_` (wildcard LIKE) matcha righe di ALTRE note → recall che perde note
altrui dopo un update.

**Fix:** escape + clausola ESCAPE:

```python
            safe_key = key.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            await db.execute(
                "DELETE FROM memory_fts WHERE user_id = ? AND kind = 'note' "
                "AND content LIKE ? ESCAPE '\\'",
                (user_id, f"{safe_key}: %"),
            )
```

**Verifica:** test: note `a_b` e `axb` per lo stesso utente; update di `a_b` → la riga FTS di `axb` sopravvive.

---

### TASK 32 — Regole orfane su rename categoria + literal duplicato
- [x] Stato: FATTO

**File:** `haria/app/memory.py` (`rename_categoria` riga ~1912, `merge_categoria` riga ~1943) e `haria/app/modules/economia.py` (guardia riga ~640).

**Problema (due nit del round 2):**
1. `rename_categoria`/`merge_categoria` non aggiornano `econ_regole`: una regola che
   punta alla categoria rinominata resta orfana e ricrea la vecchia categoria al
   prossimo match.
2. In `_gestisci_categorie` la guardia usa il literal `"trasferimento"` invece della
   costante `CATEGORIA_TRASFERIMENTO`.

**Fix:**
1. In `rename_categoria`, accanto all'UPDATE delle transazioni:
   `await db.execute("UPDATE econ_regole SET categoria=? WHERE categoria=?", (new, old_canon))`.
   Stesso in `merge_categoria` con `(dst_canon, src_canon)`.
2. In `modules/economia.py`: `from memory import CATEGORIA_TRASFERIMENTO` (aggiungere
   all'import esistente) e sostituire `"trasferimento"` nella guardia e nei messaggi.

**Verifica:** test: regola `xyz→svago`, `rename_categoria("svago","divertimento")` →
`list_regole()` mostra la regola con categoria `divertimento`.

---

### TASK 33 — Diete rilette da disco a ogni messaggio
- [x] Stato: FATTO

**File:** `haria/app/modules/food_diary.py`, `load_diets` (riga ~126) / `diet_prompt`.

**Problema:** `dynamic_prompt = diet_prompt` viene chiamata a OGNI messaggio
(`modules.prompt()` in `_build_system`): rilegge tutti i file dieta da disco ogni
volta. I/O sincrono nell'event loop + nessuna cache. Con diete corpose = latenza
su ogni singolo messaggio.

**Fix:** cache con invalidazione su mtime. In `load_diets`:

```python
_diets_cache: tuple | None = None  # (signature, contenuto)

def load_diets() -> str:
    global _diets_cache
    sig = []
    for d in _DIET_DIRS:
        if not d or not os.path.isdir(d):
            continue
        for ext in _DIET_EXTS:
            for f in glob.glob(os.path.join(d, ext)):
                try:
                    sig.append((f, os.path.getmtime(f)))
                except OSError:
                    continue
    sig = tuple(sorted(sig))
    if _diets_cache and _diets_cache[0] == sig:
        return _diets_cache[1]
    # ... (corpo attuale che legge e concatena i file) ...
    result = "\n\n".join(parts)
    _diets_cache = (sig, result)
    return result
```

Le glob/getmtime restano (economiche); si evita solo la rilettura dei contenuti.
`save_diet`/`delete_diet` invalidano da sole (mtime/lista file cambiano).

**Verifica:** test: due chiamate consecutive a `load_diets()` con DIETS_PATH temporaneo →
stesso risultato; dopo modifica di un file → contenuto aggiornato.

---

### TASK 34 — Turno utente salvato anche quando l'API fallisce
- [x] Stato: FATTO

**File:** `haria/app/claude_engine.py`, `chat()` (rami `except RateLimitError` / `except anthropic.APIError`).

**Problema:** il turno user viene salvato a inizio `chat()`. Se l'API fallisce, il
messaggio di errore mostrato all'utente NON viene salvato come turno assistant: in
history restano due (o più) turni user consecutivi, che sporcano il summary e il
contesto dei turni successivi.

**Fix:** nei due ram i `except`, salvare anche il turno assistant prima del return:

```python
    except RateLimitError:
        logger.error(...)
        msg = "⚠️ Troppe richieste in poco tempo. Riprova tra un minuto."
        await save_turn(user_id, "assistant", msg)
        return msg
    except anthropic.APIError as e:
        logger.error(...)
        msg = "Errore di comunicazione con l'AI. Riprova."
        await save_turn(user_id, "assistant", msg)
        return msg
```

**Verifica:** test con client mockato che solleva APIError → in `conversations` l'ultimo turno è `assistant`.

---

## Decisioni per Andrea (NON implementare senza ok)

- **`drop_pending_updates=True`** in `main.py` (~riga 120): i messaggi Telegram inviati
  mentre l'addon è giù/riavvia vengono SCARTATI. Evita replay storm al riavvio, ma
  "ho speso 20€" mandato durante un restart si perde in silenzio. Alternativa:
  `False` + gestione della coda arretrata. Da decidere.
- Lista "fuori scope" invariata dai round precedenti (split memory.py, MQTT fantasma,
  pin requirements, filtro entity cache, max_tokens, moduli config morti, auth webpanel).
- `dynamic_prompt` economia fa anche una query sqlite SINCRONA per messaggio
  (`_categorie_sync`/`_regole_sync`): con WAL e DB piccolo è trascurabile, ma se si
  tocca quella zona valutare una cache come TASK 33.

## Changelog fix

(una riga per task completato: data, task, commit)

- 2026-06-12 — TASK 27-34 — fix round3: reminder corrotto/cron step/throttle push/FTS escape/regole orfane/cache diete/turno assistant su errore/HA giù — v0.1.98 (169 test)
