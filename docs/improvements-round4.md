# HARIA — Round 4: migliorie approvate da Andrea (2026-06-12)

Non sono bugfix: sono le voci "fuori scope" dei round 1-3 + le "decisioni per Andrea",
discusse e APPROVATE una per una. Ogni task riporta la decisione presa: non cambiarla,
non ridiscuterla. Prerequisito: round 3 (`docs/bugfix-round3.md`) completato.

## Regole operative

- Ordine OBBLIGATORIO: TASK 35 → 42. Il 42 (split memory.py) va per ULTIMO perché
  tocca lo stesso file che altri task modificano.
- Un commit per task. Dopo ogni task: `cd haria/app && python -m pytest tests/ -q`
  (baseline: quella lasciata dal round 3; nessun test deve regredire).
- Bump `version` in `haria/config.yaml` a ogni commit.
- Stile commit: `feat(<area>): ...` o `chore(<area>): ...` in italiano.
- A task completato: spunta checkbox + riga nel Changelog in fondo.

**Decisione SENZA task** (già a posto, non fare nulla): la cache entità su DB è già
persistente e si aggiorna a riavvio/24h/`/updateentities` — Andrea ha valutato e
RIFIUTATO il filtro per dominio. Non implementare filtri sull'entity cache.

---

### TASK 35 — max_tokens del loop agentico: 1024 → 4096
- [x] Stato: FATTO

**Decisione:** Andrea ha approvato 4096 (risposte lunghe troncate oggi; costo extra
trascurabile, si paga solo l'output effettivo).

**File:** `haria/app/claude_engine.py`, `chat()` (riga ~318, `max_tokens=1024`).

**Fix:** `max_tokens=4096`. Lasciare invariato il `max_tokens=400` di `_maybe_summarize`
(il riassunto DEVE restare corto) e il 900 di `news.generate`.

---

### TASK 36 — Prompt caching con TTL 1 ora
- [x] Stato: FATTO

**Decisione:** Andrea vuole ridurre il ripagamento della lista entità nelle conversazioni
riprese: TTL del prompt cache da 5 min (default) a 1 ora. NIENTE filtri sulla lista.

**File:** `haria/app/claude_engine.py` — `get_tools()` (riga ~127) e `_build_system` (riga ~252).

**Fix:**
1. Nei due punti con `"cache_control": {"type": "ephemeral"}` passare a
   `{"type": "ephemeral", "ttl": "1h"}`.
2. Il TTL 1h richiede il beta header: nelle chiamate `client.messages.create` di `chat()`
   aggiungere `extra_headers={"anthropic-beta": "extended-cache-ttl-2025-04-11"}`.
   (NON serve in `_maybe_summarize` né in `news.generate`: lì non c'è cache_control.)
3. Verificare la versione SDK: `anthropic>=0.40` accetta `ttl` nel dict cache_control
   (è un dict libero lato SDK, va passato com'è). Se il server rifiuta il ttl,
   l'errore è esplicito: in tal caso aggiornare il pacchetto `anthropic` (vedi TASK 38).

**Nota costi:** scrittura cache 2× invece di 1,25×, letture 0,1× invariate. Approvato.

**Verifica:** avvio reale non necessario; basta test che `get_tools()[-1]["cache_control"]["ttl"] == "1h"` e che il blocco system[0] abbia ttl 1h.

---

### TASK 37 — Recupero messaggi Telegram arrivati durante i riavvii
- [x] Stato: FATTO

**Decisione:** Andrea ha scelto il recupero completo degli arretrati (oggi vengono scartati).

**File:** `haria/app/main.py` (riga ~120).

**Fix:** `await app.updater.start_polling(drop_pending_updates=False)`.

**Comportamento accettato:** dopo un downtime lungo HARIA processa in fila i messaggi
arretrati (risposte tardive possibili). Nessuna mitigazione richiesta.

---

### TASK 38 — Pin esatto delle dipendenze
- [ ] Stato: da fare

**Decisione:** pin `==` su tutte le dipendenze (build addon riproducibile; ddgs è
recidivo nei breaking change).

**File:** `haria/app/requirements.txt`.

**Procedura:**
1. Ambiente pulito: `python -m venv /tmp/haria-pin && /tmp/haria-pin/bin/pip install -r haria/app/requirements.txt`
   (su Windows adattare i path). In alternativa usare le versioni già installate
   nell'ambiente di test corrente se i test passano.
2. `pip freeze` e ricavare le versioni dei SOLI 11 pacchetti top-level elencati nel
   requirements (non congelare le dipendenze transitive: il base image Alpine può
   richiedere build proprie).
3. Riscrivere requirements.txt con `==` (es. `ddgs==9.x.y` — la versione vera è quella
   del freeze, NON inventarla).
4. Girare l'intera suite con quelle versioni installate.

**Nota:** futuri aggiornamenti = alzare il pin deliberatamente e testare.

---

### TASK 39 — Auth del webpanel: verifica header ingress, dietro toggle
- [ ] Stato: da fare

**Decisione:** verifica ingress configurabile via feature toggle (come gli altri flag),
default ATTIVO.

**File:** `haria/app/webpanel.py`, `haria/config.yaml`.

**Fix:**
1. `config.yaml`: nuova option `panel_auth: true` (in `options`) e `panel_auth: bool?`
   (in `schema`) — fuori dal blocco `modules`, è una impostazione di sicurezza.
2. In `webpanel.py` middleware aiohttp registrato in `build_web_app()`:

```python
from aiohttp import web

INGRESS_GATEWAY_IP = "172.30.32.2"  # proxy ingress del Supervisor

@web.middleware
async def _ingress_only(request, handler):
    if not cfg.get("panel_auth", True):
        return await handler(request)
    peer = request.remote or ""
    has_ingress_header = bool(request.headers.get("X-Ingress-Path"))
    if has_ingress_header or peer == INGRESS_GATEWAY_IP or peer in ("127.0.0.1", "::1"):
        return await handler(request)
    return web.Response(status=401, text="Accesso solo via ingress Home Assistant")
```

   e `web.Application(middlewares=[_ingress_only])`.
3. Localhost resta ammesso (debug in container).

**Verifica:** test con aiohttp test client: richiesta senza header e remote fittizio →
401; con header `X-Ingress-Path: /x` → 200; con `panel_auth: false` mockata → 200 sempre.
ATTENZIONE: i test esistenti che usano il webpanel (se ci sono) vanno adattati passando
l'header o mockando cfg.

---

### TASK 40 — Implementare i flag `telegram` / `ha_chat` / `voice`
- [ ] Stato: da fare

**Decisione:** Andrea vuole i 3 flag RISPETTATI dal codice (oggi sono nello schema ma ignorati).

**File:** `haria/app/main.py`, `haria/app/webpanel.py`, `haria/app/telegram_handler.py`.

**Semantica concordata:**
- `modules.telegram: false` → non acquisire il bot, niente polling, niente release.
  `notifier.set_bot` non chiamato → promemoria/briefing/messaggi proattivi via Telegram
  NON consegnabili: `scheduler` e `notifier` loggano warning chiaro («modulo telegram
  disabilitato») invece di crashare (oggi `_bot.send_message` esploderebbe con `_bot=None`
  → proteggere i punti d'uso). Il pannello web e i suoi tool continuano a funzionare.
  In `main()`: l'app PTB non viene proprio costruita; lo scheduler parte comunque
  (serve a MQTT/cron economia).
- `modules.ha_chat: false` → in `webpanel.build_web_app()` NON registrare le route
  `/chat` e `/api/chat`; togliere la voce Chat dalla nav (`_page`) e la tile dalla home.
- `modules.voice: false` → `_handle_voice` risponde "Trascrizione vocale disabilitata
  nella configurazione." prima ancora di controllare groq_key.

**⚠️ ATTENZIONE config di Andrea:** in `config.yaml` il default di `ha_chat` è `false`.
Finché il flag era ignorato la chat web funzionava comunque; dopo questo task con
default false SI SPEGNE. Andrea usa la chat ingress → cambiare il default a
`ha_chat: true` nelle options di config.yaml come parte di questo task, e scrivere nel
commit message che chi la vuole spenta la disattiva esplicitamente.

**Verifica:** test webpanel: con ha_chat false `/api/chat` → 404. Test scheduler: `_fire`
con bot None → nessuna eccezione, warning loggato, reminder one-shot NON disattivato.

---

### TASK 41 — Cleanup automatico entità MQTT fantasma
- [ ] Stato: da fare

**Decisione:** cleanup automatico (no tool manuale).

**File:** `haria/app/mqtt_pub.py`, `haria/app/memory.py` (nuova tabella).

**Problema:** budget/obiettivi/conti eliminati (e membri food rimossi) lasciano in HA
sensori morti: il config topic discovery e lo state topic restano retained per sempre.

**Fix (meccanismo):** registro dei topic pubblicati + diff a ogni publish.
1. `memory.py`: tabella `mqtt_topics (uid TEXT PRIMARY KEY, config_topic TEXT NOT NULL,
   state_topics TEXT NOT NULL, kind TEXT NOT NULL)` (state_topics = JSON array; kind =
   'economia'|'bollette'|'food') + funzioni `get_mqtt_topics(kind)`, `set_mqtt_topics(kind, rows)`.
2. `mqtt_pub.py`: `_disc_sensor` (o i suoi chiamanti) accumulano i uid/topic pubblicati
   nel giro corrente. A fine `publish_economia()` / `publish_bollette()` /
   `publish_discovery()`+`refresh()`:
   - leggere il registro per quel kind;
   - per ogni uid nel registro ma NON nel giro corrente: `_pub(config_topic, "", retain=True)`
     (payload vuoto sul config topic = HA elimina l'entità) e `_pub(state_topic, "", retain=True)`
     per ogni state topic (svuota il retained);
   - salvare il giro corrente come nuovo registro.
3. Per le bollette NON rimuovere gli anni passati (i sensori storici sono voluti): il
   set corrente li include già (range continuo + anni con dati), quindi il diff è
   naturalmente vuoto — nessun caso speciale necessario, solo non "ottimizzare" il range.

**Verifica:** test su memory (registro get/set) + test mqtt_pub con `_pub` mockata:
primo publish con 2 budget → registro 2 uid; secondo publish con 1 budget → `_pub`
chiamata con payload "" sul config topic del budget sparito e registro aggiornato.

---

### TASK 42 — Split di memory.py in package (PER ULTIMO)
- [ ] Stato: da fare

**Decisione:** split con facade, zero modifiche nei file consumatori.

**File:** `haria/app/memory.py` → package `haria/app/memory/`.

**Struttura:**
```
memory/
  __init__.py     # facade: from .core import *; from .food import *; ecc.
                  # + DB_PATH, MAX_HISTORY, SUMMARY_BATCH, CATEGORIA_TRASFERIMENTO
  core.py         # init_db (TUTTO lo schema resta qui), history, note, summary,
                  # FTS/search_memory, entity_cache, _connect/PRAGMA
  misc.py         # reminder, briefing, news_blocklist, error_log
  food.py         # profili, peso, pasti, piano, idratazione, spesa, dispensa, food_cache
  bollette.py     # set_bolletta*, get_bolletta*, seed
  econ.py         # conti, transazioni, categorie, regole, budget, obiettivi,
                  # riepiloghi, mqtt_topics (dal TASK 41)
```

**Vincoli:**
- `memory/__init__.py` ri-esporta TUTTO ciò che il vecchio modulo esponeva: `grep -rn "from memory import" haria/app/` per la lista completa dei nomi usati; nessun altro file va modificato.
- `init_db()` resta UNA funzione in core.py con l'intero executescript (lo schema non si spezza: l'ordine delle migrazioni conta).
- Lo stato condiviso `_FTS_OK` resta in core; i moduli che ne hanno bisogno lo importano da core (attenzione: import del VALORE al momento dell'import = bug; usare `core._FTS_OK` via modulo o una funzione `fts_ok()`).
- `DB_PATH` deve restare monkeypatchabile dai test: `monkeypatch.setattr(memory, "DB_PATH", ...)` nel conftest — i sottomoduli NON devono leggere `DB_PATH` a import time ma a runtime via funzione/modulo (es. `core.db_path()` o `from . import core` + `core.DB_PATH`). Verificare che il conftest esistente continui a funzionare SENZA modifiche; se impossibile, adattare SOLO il conftest e dichiararlo nel commit.
- Refactor MECCANICO: nessuna firma cambia, nessuna logica cambia.
- Suite completa verde prima del commit; questo task è l'unico del round senza bump di comportamento — commit `chore(memory): split in package per dominio (facade, API invariata)`.

---

## Changelog fix

(una riga per task completato: data, task, commit)
