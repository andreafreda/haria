# HARIA — Bug e fix da applicare (review 2026-06-11, v0.1.90)

Documento operativo per agenti/modelli che applicano i fix.

## Regole generali per chi lavora su questo file

- Lavora su UN task alla volta. Ogni task è autocontenuto.
- Percorsi relativi alla root del repo (`C:\projects\haria`).
- Dopo ogni fix esegui i test: `cd haria/app && python -m pytest tests/ -q`.
- Non cambiare comportamento non menzionato nel task. Non riformattare codice circostante.
- Stile commit: `fix(<area>): <descrizione breve>` (italiano, come lo storico del repo).
- Quando un task è completato, spunta la checkbox qui e aggiungi una riga in fondo al file nella sezione "Changelog fix".

---

## 🔴 PRIORITÀ 1 — Dati utente a rischio

### TASK 1 — Conti custom inutilizzabili
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/econ_def.py` (funzione `norm_conto`, riga ~92) e `haria/app/modules/economia.py`.

**Problema:** `gestisci_conti` con azione `crea` inserisce un conto nuovo nel DB (es. `revolut_marina`), ma `norm_conto()` risolve solo contro il dict statico `CONTI`. Quindi `add_transazione`, `get_saldo` e il filtro di `gestisci_transazioni` rispondono "Conto non riconosciuto" sul conto appena creato.

**Fix:** `norm_conto` è sync e non può interrogare il DB. La risoluzione va completata nei punti di uso in `modules/economia.py`: dove oggi si fa

```python
conto = norm_conto(conto_raw, membro)
if conto is None:
    return f"Conto '{conto_raw}' non riconosciuto. ..."
```

aggiungere, PRIMA di dichiarare il conto non riconosciuto, un fallback sul DB:

```python
conto = norm_conto(conto_raw, membro)
if conto is None:
    row = await memory.get_conto(conto_raw.strip().lower())
    if row is not None:
        conto = row["nome"]
if conto is None:
    return f"Conto '{conto_raw}' non riconosciuto. ..."
```

Punti da toccare in `modules/economia.py`: `_add_transazione`, `_get_saldo`, `_riepilogo_spese`, `_gestisci_transazioni` (sia ramo `lista` sia ramo `modifica`).

**Verifica:** test nuovo in `haria/app/tests/`: crea conto via `memory.add_conto("revolut_test", "carta")`, poi chiama `economia.handle("add_transazione", {"tipo": "spesa", "importo": 10, "categoria": "svago", "conto": "revolut_test"}, "123")` e verifica che il risultato contenga `"ok": true`.

---

### TASK 2 — Migrazione distruttiva eseguita a ogni avvio
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/memory.py`, dentro `init_db()`, riga ~227.

**Problema:** questo statement gira a OGNI avvio:

```python
await db.execute(
    "UPDATE econ_conti SET attivo=0 WHERE nome IN ('postepay','paypal','contanti')"
)
```

Se l'utente crea un conto chiamato `paypal`, al riavvio successivo viene disattivato silenziosamente.

**Fix:** rendere la migrazione una-tantum. Creare una tabella `schema_migrations (id TEXT PRIMARY KEY)` in `init_db` e wrappare lo statement:

```python
cur = await db.execute("SELECT 1 FROM schema_migrations WHERE id='deactivate_generic_conti'")
if (await cur.fetchone()) is None:
    await db.execute(
        "UPDATE econ_conti SET attivo=0 WHERE nome IN ('postepay','paypal','contanti')"
    )
    await db.execute("INSERT INTO schema_migrations (id) VALUES ('deactivate_generic_conti')")
```

**Verifica:** test: `init_db()`, poi `add_conto("paypal", "wallet")`, poi `init_db()` di nuovo → `get_conto("paypal")["attivo"]` deve restare `True`.

---

### TASK 3 — `reset_economia` cancella i salvadanai senza avvisare
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/modules/economia.py`, funzione `_reset_economia` (riga ~502), e `haria/app/memory.py`, funzione `reset_economia` (riga ~2007).

**Problema:** `memory.reset_economia` fa `DELETE FROM econ_obiettivi` incondizionato, ma il messaggio di anteprima (ramo `confirm=False`) elenca solo transazioni/categorie/saldi. L'utente conferma e perde gli obiettivi senza saperlo.

**Fix (scelta A, preferita):** aggiornare il messaggio di anteprima in `_reset_economia` aggiungendo ", cancella tutti i salvadanai/obiettivi" al testo `msg`, e includere `"obiettivi_attuali": await get_obiettivi()` nel JSON di anteprima accanto a `saldi_attuali`.

**Verifica:** chiamare `economia.handle("reset_economia", {}, "123")` con un obiettivo presente → la risposta JSON deve menzionare gli obiettivi.

---

### TASK 4 — Dedup import scarta movimenti legittimi identici
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/econ_import.py` (funzione `parse`, riga ~156) e relativo hash.

**Problema:** l'hash di dedup è `conto|data|importo|descrizione`. Due movimenti REALI identici nello stesso estratto (es. due caffè da €1,00 stesso giorno, stesso esercente) producono lo stesso hash: in `memory.import_transazioni` il primo viene inserito e aggiunto a `existing`, il secondo viene scartato come duplicato. Perdita dati silenziosa.

**Fix:** disambiguare le occorrenze DENTRO lo stesso file in `econ_import.parse`: tenere un `collections.Counter` delle chiavi e aggiungere l'indice di occorrenza all'hash:

```python
seen_keys: dict[str, int] = {}
...
key = f"{conto}|{data}|{importo:.2f}|{_norm(desc)}"
n = seen_keys.get(key, 0)
seen_keys[key] = n + 1
h = hashlib.sha1(f"{key}|{n}".encode("utf-8")).hexdigest()
```

Adattare `row_hash` o inline. Così il re-import dello stesso file resta dedup-safe (stessi indici → stessi hash), ma due righe identiche nel file producono hash diversi.

**Attenzione:** cambia gli hash con suffisso `|0` rispetto a quelli storici in DB. Per non far ricomparire vecchi movimenti come "nuovi", mantenere la compatibilità: la prima occorrenza (n=0) deve usare l'hash VECCHIO senza suffisso:

```python
h = row_hash(conto, data, importo, desc) if n == 0 \
    else hashlib.sha1(f"{key}|{n}".encode("utf-8")).hexdigest()
```

**Verifica:** estendere `tests/test_econ_import.py`: file con due righe identiche → `parse()` deve produrre 2 movimenti con hash diversi; reimport dello stesso file → 0 inseriti, 2 duplicati.

---

### TASK 5 — Data transazione non validata
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/modules/economia.py`, funzioni `_add_transazione` (riga ~629) e `_gestisci_transazioni` ramo `modifica` (riga ~387).

**Problema:** la data arriva dal modello e finisce in DB senza validazione. Una data tipo `12/06/2026` rompe le aggregazioni che fanno `substr(data, 6, 2)` e `LIKE 'YYYY-MM-%'` (`andamento_mensile`, `get_budget_status`, `spese_categoria_anno`).

**Fix:** in entrambi i punti, validare con `date.fromisoformat`:

```python
data = (inputs.get("data") or "").strip()
if data:
    try:
        date.fromisoformat(data)
    except ValueError:
        return "Data non valida: usa il formato YYYY-MM-DD."
else:
    data = date.today().isoformat()
```

Nel ramo `modifica` analogamente per `kw["data"]`.

**Verifica:** test: `add_transazione` con `data="12/06/2026"` → risposta di errore, nessuna riga inserita.

---

## 🟠 PRIORITÀ 2 — Comportamento errato visibile

### TASK 6 — Refresh MQTT lanciato prima della mutazione
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/modules/food_diary.py`, funzione `handle` (riga ~823).

**Problema:** `_maybe_refresh_mqtt(name)` è chiamato all'INIZIO di `handle()`, prima della scrittura su DB. Il task di refresh parte al primo `await` e può leggere lo stato pre-commit → sensori HA stale fino al refresh periodico (5 min).

**Fix:** spostare la chiamata a fine esecuzione. Modo più semplice senza toccare i 30 rami: wrappare:

```python
async def handle(name: str, inputs: dict, user_id: str) -> str:
    result = await _handle_inner(name, inputs, user_id)
    _maybe_refresh_mqtt(name)
    return result
```

dove `_handle_inner` è l'attuale corpo di `handle` SENZA la riga `_maybe_refresh_mqtt(name)`.

**Verifica:** i test esistenti devono passare; controllo manuale che `_maybe_refresh_mqtt` non sia più chiamato prima dei rami.

---

### TASK 7 — One-shot reminder perso se l'invio Telegram fallisce
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/scheduler.py`, funzione `_fire` (riga ~48).

**Problema:** il reminder one-shot viene disattivato anche quando `send_message` solleva eccezione: Telegram irraggiungibile per 30 secondi = promemoria perso per sempre.

**Fix:** disattivare solo a invio riuscito; in caso di errore riprovare una volta dopo 60s:

```python
async def _fire(reminder_id: int, user_id: str, message: str, recurring: str | None):
    sent = False
    for attempt in range(2):
        try:
            await _bot.send_message(chat_id=int(user_id), text=f"⏰ Promemoria: {message}")
            sent = True
            break
        except Exception as e:
            logger.error("Invio promemoria %s fallito (tentativo %d): %s", reminder_id, attempt + 1, e)
            if attempt == 0:
                import asyncio
                await asyncio.sleep(60)
    if not recurring and sent:
        await deactivate_reminder(reminder_id)
```

Nota: se anche il retry fallisce, il one-shot resta attivo e verrà ricaricato al prossimo riavvio (comportamento accettato).

---

### TASK 8 — Ordinamento history per timestamp (risoluzione 1 secondo)
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/memory.py`, funzione `get_history` (riga ~330).

**Problema:** `ORDER BY timestamp DESC` — user e assistant salvati nello stesso secondo possono invertirsi nell'ordine inviato al modello.

**Fix:** cambiare in `ORDER BY id DESC LIMIT ?`. Una riga.

---

### TASK 9 — Sensori economia/bollette stale a cavallo del mese
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/scheduler.py` (funzione `schedule_mqtt_refresh`, riga ~263) e `haria/app/main.py` (riga ~140).

**Problema:** `publish_economia`/`publish_bollette` girano solo a startup e dopo mutazioni. Il 1° del mese i sensori `spese_mese`, budget e confronto mese precedente mostrano il mese vecchio finché qualcuno non registra una spesa.

**Fix:** in `scheduler.py` aggiungere:

```python
def schedule_econ_refresh():
    """Ripubblica i sensori economia+bollette ogni notte alle 00:05."""
    if not _scheduler:
        return
    async def _job():
        try:
            import mqtt_pub
            await mqtt_pub.publish_economia()
            await mqtt_pub.publish_bollette()
        except Exception as e:
            logger.debug("Refresh economia/bollette fallito: %s", e)
    _scheduler.add_job(_job, CronTrigger(hour=0, minute=5),
                       id="econ_refresh", replace_existing=True)
```

In `main.py`, dopo `await mqtt_pub.start()`, se `economia` o `bollette` abilitati: chiamare `scheduler.schedule_econ_refresh()`. ATTENZIONE: oggi lo scheduler parte solo se agenda/food_diary/news abilitati (riga ~125). Estendere la condizione di `scheduler.start(app.bot)` per includere `bollette` ed `economia`.

---

### TASK 10 — Briefing dal pannello web: errore fuorviante se scheduler spento
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/main.py` (riga ~125) e `haria/app/webpanel.py`, `_h_briefings_save` (riga ~826).

**Problema:** lo scheduler parte solo con agenda/food_diary/news. Col fix del TASK 9 partirà quasi sempre, ma se TUTTI i moduli sono off il pannello briefing mostra "Cron non valido" quando in realtà lo scheduler non è avviato.

**Fix:** in `webpanel._h_briefings_save`, prima di chiamare `scheduler.schedule_briefing`, distinguere i casi: se `scheduler._scheduler is None` (esporre helper `scheduler.is_running() -> bool` invece di accedere al privato) ritornare errore `"Scheduler non attivo: abilita almeno un modulo tra agenda/food_diary/news"` con status 503.

---

### TASK 11 — `respond` + altri tool nello stesso turno: risultati buttati
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/claude_engine.py`, funzione `chat`, loop righe ~329-348.

**Problema:** se il modello chiama `respond` E un altro tool nello stesso messaggio, l'altro tool viene ESEGUITO (side effect reale) ma il risultato è scartato e la risposta è restituita subito: il modello non saprà mai se il tool è riuscito.

**Fix:** se nel response ci sono sia `respond` sia altri tool_use, ignorare `respond` (non restituirlo), eseguire gli altri tool e continuare il loop con i tool_results, così il modello richiama `respond` al giro dopo con i risultati in mano:

```python
if reply is not None and tool_results:
    # respond prematuro insieme ad altri tool: continua il loop coi risultati
    reply = None
if reply is not None:
    ...
```

Inserire il blocco dopo il for sui blocchi e prima del `if reply is not None`.

---

### TASK 12 — Webhook HA ripristinato senza secret
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/main.py`, `_release_bot` (riga ~82).

**Problema:** `getWebhookInfo` di Telegram non restituisce `secret_token`, quindi `_previous_webhook.get("secret_token")` è sempre `None` e il webhook viene ripristinato senza secret. Se l'integrazione HA usava un secret, rifiuterà gli update dopo lo stop di HARIA.

**Fix minimo (documentale):** non si può recuperare il secret dall'API. Aggiungere al log un warning esplicito:

```python
logger.warning(
    "Webhook HA ripristinato SENZA secret_token (Telegram non lo espone): "
    "se l'integrazione HA usava un secret, va riconfigurata."
)
```

e rimuovere il parametro `secret` morto da `_set_webhook`/`_release_bot` (o lasciarlo ma documentare). Nessun fix funzionale possibile.

---

## 🟡 PRIORITÀ 3 — Robustezza e pulizia

### TASK 13 — WAL + busy_timeout su SQLite
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/memory.py`, `init_db()`.

**Problema:** nessun PRAGMA: con webpanel + Telegram + scheduler + refresh MQTT concorrenti si rischia `database is locked`.

**Fix:** all'inizio di `init_db`, prima dell'executescript:

```python
await db.execute("PRAGMA journal_mode=WAL")
await db.execute("PRAGMA busy_timeout=5000")
```

`journal_mode=WAL` è persistente nel file DB (basta una volta); `busy_timeout` è per-connessione: per coprire TUTTE le connessioni, creare un helper:

```python
async def _connect():
    db = await aiosqlite.connect(DB_PATH)
    await db.execute("PRAGMA busy_timeout=5000")
    return db
```

NON serve sostituire tutti i `aiosqlite.connect(DB_PATH)` in questo task (sono ~80): basta applicare il PRAGMA in `init_db` e creare l'helper per usi futuri. Sostituzione completa = task separato opzionale.

---

### TASK 14 — Task asyncio fire-and-forget senza reference
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/mqtt_pub.py`, funzioni `request_refresh`, `request_economia_refresh`, `request_bollette_refresh` (righe ~544-578).

**Problema:** `loop.create_task(...)` senza tenere il riferimento: il garbage collector può cancellare il task a metà (pitfall asyncio documentato).

**Fix:** aggiungere a livello modulo:

```python
_bg_tasks: set = set()

def _spawn(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("spawn: nessun event loop attivo, skip")
        return
    t = loop.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)
```

e usare `_spawn(refresh())`, `_spawn(publish_economia())`, `_spawn(publish_bollette())` nelle tre funzioni, eliminando il codice duplicato try/except in ciascuna.

---

### TASK 15 — `paho.connect()` bloccante nell'event loop
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/mqtt_pub.py`, funzione `start` (riga ~121).

**Problema:** `c.connect(...)` è I/O sincrono dentro una coroutine: broker lento/irraggiungibile blocca l'intero event loop (Telegram muto).

**Fix:** spostare connect in thread:

```python
await asyncio.to_thread(c.connect, conf["host"], int(conf.get("port", 1883)), 60)
```

---

### TASK 16 — Distribuzione bolletta su più mesi non conserva il totale
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/memory.py`, funzione `set_bolletta_range` (riga ~1415).

**Problema:** `per = round(total/n, 1)` su ogni mese: €100 su 3 mesi → 33.3×3 = 99.9, si perdono centesimi.

**Fix:** l'ultimo mese assorbe il resto:

```python
per = round(float(total) / n, 1)
last_val = round(float(total) - per * (n - 1), 1)
# nel loop: value = last_val se mth == m_end else per
```

**Verifica:** test: range 3 mesi, totale 100 → somma dei 3 valori == 100.0.

---

### TASK 17 — Rename conto può crashare su collisione UNIQUE
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/memory.py`, funzione `update_conto` (riga ~1543).

**Problema:** `nuovo_nome` uguale a un conto esistente → `sqlite3.IntegrityError` non gestita → errore grezzo fino al tool result.

**Fix:** wrappare l'execute:

```python
try:
    cur = await db.execute(...)
except aiosqlite.IntegrityError:
    return False
```

(meglio: far risalire un messaggio chiaro — accettabile anche solo `False`).

---

### TASK 18 — Media non gestiti per utenti non autorizzati: silenzio
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/telegram_handler.py`, `_handle_voice` (riga ~154), `_handle_photo` (riga ~192), `_handle_document` (riga ~223).

**Problema:** messaggio di testo da utente non autorizzato → risposta "Non sei autorizzato". Vocale/foto/documento → silenzio totale. Incoerente.

**Fix:** in tutti e tre, sostituire il `return` muto con:

```python
if chat_id not in users:
    await update.message.reply_text("Non sei autorizzato a usare HARIA.")
    return
```

---

### TASK 19 — Validazione date export CSV
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/webpanel.py`, `_h_export` (riga ~926).

**Problema:** `from`/`to` dalla query string finiscono non validati nel filename del Content-Disposition.

**Fix:** validare con `date.fromisoformat` e fallback al default su `ValueError`:

```python
try:
    df = date.fromisoformat(request.query.get("from", "")).isoformat()
except ValueError:
    df = start.isoformat()
```

(idem per `to`).

---

### TASK 20 — Formato euro anglosassone nel pannello
- [x] Stato: FATTO (v0.1.92)

**File:** `haria/app/webpanel.py`, funzione `_eur` (riga ~188) e i punti con f-string `€{...:,.2f}` / `€{...:,.0f}` (righe ~312).

**Problema:** `{v:,.2f}` produce `1,234.56` — in UI italiana ci si aspetta `1.234,56`.

**Fix:** helper:

```python
def _it_num(v: float, dec: int = 2) -> str:
    s = f"{v:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")
```

usarlo in `_eur` e nei KPI budget.

---

## 📋 NON in scope (decisioni da prendere con Andrea, non fixare da soli)

- Split di `memory.py` (2.174 righe) in package per dominio.
- Rimozione entità MQTT fantasma per budget/obiettivi/conti eliminati (serve publish vuoto sul config topic discovery — design da decidere).
- Pin esatto delle dipendenze in `requirements.txt` (testare prima la build addon).
- Filtro domini entity cache nel system prompt (riduzione token).
- `max_tokens=1024` nel loop agentico (alzare costa: decidere budget).
- Moduli `telegram`/`ha_chat`/`voice` in config.yaml mai letti dal codice.
- Auth propria del webpanel (oggi protetto solo da ingress HA).

## Changelog fix

- 2026-06-11 — TASK 1-20 applicati in blocco (v0.1.92, commit unico). Note:
  - TASK 1: `_resolve_conto` (norm_conto + fallback DB) in add_transazione/get_saldo/riepilogo/gestisci_transazioni.
  - TASK 2: migrazione conti generici wrappata in `schema_migrations` (una-tantum).
  - TASK 3: anteprima reset elenca budget+salvadanai (+`obiettivi_attuali`).
  - TASK 4: hash dedup per-occorrenza (n=0 = hash storico, retrocompat).
  - TASK 5: validazione data ISO in add_transazione e gestisci_transazioni/modifica.
  - TASK 6: food_diary `_handle_inner` + refresh MQTT post-mutazione.
  - TASK 7: `_fire` retry 60s, disattiva one-shot solo se inviato.
  - TASK 8: `get_history` ORDER BY id.
  - TASK 9: `schedule_econ_refresh` (00:05) + start scheduler/MQTT estesi a economia/bollette.
  - TASK 10: `scheduler.is_running()` + 503 nel pannello briefing.
  - TASK 11: respond+altri tool → ignora respond prematuro, continua loop coi risultati.
  - TASK 12: log warning webhook senza secret (no fix funzionale possibile).
  - TASK 13: PRAGMA journal_mode=WAL + busy_timeout in init_db (helper full-replace non fatto, opzionale).
  - TASK 14: `_spawn` con set di reference per i task background MQTT.
  - TASK 15: `paho.connect` via `asyncio.to_thread`.
  - TASK 16: `set_bolletta_range` ultimo mese assorbe il resto (totale conservato).
  - TASK 17: `update_conto` cattura IntegrityError su collisione UNIQUE.
  - TASK 18: risposta "Non autorizzato" anche su voce/foto/documento.
  - TASK 19: validazione date export CSV con fallback.
  - TASK 20: formato € italiano (`_it_num`) in `_eur` e KPI budget.
  - +8 test di verifica. Suite: 144 passed.
