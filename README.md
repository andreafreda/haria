# HARIA — Home Assistant Reactive Intelligent Agent

Addon Home Assistant: assistente AI personale basato su Claude (Anthropic),
accessibile via **Telegram** e via **pannello web ingress** in HA. Controlla la
casa, gestisce agenda/promemoria, diario alimentare famigliare, bollette, ricerca
web, e mantiene una **memoria a lungo termine** per ogni utente.

Nome = acronimo **H**ome **A**ssistant **R**eactive **I**ntelligent **A**gent. Si pronuncia "Aria".

Repo: https://github.com/andreafreda/haria — versione corrente: **v0.1.50**.

---

## Indice

- [Come funziona (architettura)](#come-funziona-architettura)
- [Interfacce](#interfacce)
- [Memoria](#memoria)
- [Moduli e feature](#moduli-e-feature)
- [Tool disponibili a Claude](#tool-disponibili-a-claude)
- [Database](#database)
- [Configurazione](#configurazione)
- [Deploy](#deploy)
- [Stack tecnologico](#stack-tecnologico)
- [Roadmap / residui](#roadmap--residui)

---

## Come funziona (architettura)

```
UTENTE (Telegram testo/vocale/foto/PDF  |  pannello web HA)
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  HARIA Core (Python, addon Docker)                        │
│                                                            │
│  telegram_handler.py ─┐                                    │
│  webpanel.py /api/chat┘──▶ claude_engine.chat()           │
│                              │  (agentic loop, tool calling)│
│        ┌─────────────────────┼──────────────────────────┐ │
│        ▼            ▼         ▼            ▼            ▼  │
│   CORE tools   moduli     memoria      scheduler     MQTT  │
│   (casa, ...)  (registry) (SQLite)     (APScheduler) (food)│
└────────┼───────────────────────────────────────────────────┘
         ▼
   HA REST / WebSocket API
```

**Flusso di una richiesta** (`claude_engine.chat`):
1. `_maybe_summarize` — se la history è troppo lunga, piega i turni vecchi in un riassunto.
2. Recupera history recente (ultimi `MAX_HISTORY=10` turni) + salva il turno utente.
3. Costruisce il system prompt (`_build_system`): identità + regole + lista entità HA
   (cached) + **note salvate** + **riassunto conversazioni** + data/ora settimana.
4. Agentic loop (max `MAX_TURNS=8`): Claude sceglie tool → HARIA esegue → ripete finché
   chiama `respond`. Ultimo giro forza `respond` (no loop infiniti).
5. Salva la risposta, la invia (Telegram spezza a 4096 char, plain text).

**Vocale**: audio Telegram → Groq Whisper (`whisper-large-v3-turbo`, IT) → testo → chat.
**Foto**: passata a Claude vision; se contiene un barcode (`pyzbar`) → `lookup_barcode`.
**PDF**: passato come documento; Claude classifica (bolletta → `update_bill`, dieta → `save_diet`).

### Registry moduli

Ogni modulo in `app/modules/<x>.py` espone un contratto uniforme:

```python
NAME   = "x"                                  # chiave in config.modules
TOOLS  = [ {...schema Anthropic...} ]         # tool del modulo
PROMPT = "...frammento system prompt..."      # iniettato se abilitato
async def handle(name, inputs, user_id) -> str  # esegue i propri tool
# opzionale: dynamic_prompt() -> str          # prompt che cambia a runtime
```

`modules/__init__.py` mantiene `ALL`; `_enabled()` filtra su `config.modules[NAME]`.
I tool dei moduli abilitati sono concatenati ai CORE tool; il dispatch è automatico
per nome del tool. Aggiungere una feature = nuovo file modulo + voce in `ALL` + flag config.

---

## Interfacce

- **Telegram** (`telegram_handler.py`) — interfaccia principale. Polling (non webhook).
  Comandi: `/start` (mostra chat_id se non autorizzato), `/reset` (cancella memoria conversazione),
  `/updateentities` (ricarica cache entità HA), `/reloadconfig` (rilegge config da file).
  Whitelist su `chat_id`: chi non è in `users[]` riceve rifiuto.
- **Pannello web** (`webpanel.py`, ingress porta 8099) — dashboard food sola-lettura
  (Piano/Mese/Diario/Profili/Spesa/Dispensa/Export CSV) + pagina **Chat** con HARIA.
  Output HTML escaped (`_e()`, anti-XSS).

### Handover bot Telegram (automatico)
All'avvio HARIA salva e rimuove il webhook del bot HA (`deleteWebhook`, no drop messaggi),
prende il polling esclusivo; allo stop ripristina il webhook. Nessun intervento manuale.

---

## Memoria

Tre livelli, tutti in SQLite (`/config/haria.db`), per `user_id` (= chat_id):

1. **History conversazione** (`conversations`) — ultimi `MAX_HISTORY=10` turni inviati a
   ogni richiesta.
2. **Riassunto a lungo termine** (`conv_summary`) — quando la history supera
   `MAX_HISTORY + SUMMARY_BATCH` (30 turni), i turni vecchi vengono **piegati in un riassunto**
   via Haiku (`_maybe_summarize`) e rimossi dalla history raw. Il riassunto è iniettato nel
   system prompt → contesto storico senza gonfiare i token.
3. **Note / preferenze** (`notes`, key-value per utente) — salvate con `save_memory`,
   **auto-iniettate nel system prompt** ogni turno (Claude le vede senza dover chiamare
   `get_memory`).

**Recall keyword** (`recall` tool) — tabella **FTS5** `memory_fts` indicizza note +
conversazioni (anche quelle piegate nel riassunto e rimosse dalla history). Claude la
interroga per ricordare fatti vecchi. Best-effort: se il build SQLite non ha FTS5, degrada
a vuoto senza errori (`_FTS_OK`).

`/reset` cancella history + riassunto dell'utente; FTS resta (recall storico).

---

## Moduli e feature

Ogni modulo è on/off da `config.modules`.

### `agenda` — promemoria, task, calendario
- **Promemoria** one-shot e ricorrenti (cron) via APScheduler: `set_reminder`,
  `list_reminders`, `update_reminder`, `cancel_reminder`. Persistiti, ricaricati all'avvio.
- **Task / to-do** su liste HA native (`todo.cose_da_fare`/`promemoria`/`spesa`):
  `add_task`/`update_task`/`complete_task`. Multi-owner (`owners[]` → "👤 a, b" in description).
- **Calendario** (`calendar.haria_andrea`/`_marina`, local_calendar): `add_event`,
  `get_events`, `update_event`, `delete_event` (via REST + WebSocket per uid/delete/update).
  Multi-owner = evento su ogni calendario owner; nessun owner = famiglia.
- **`agenda_overview`** — vista unica reminder + task aperti + eventi prossimi.

### `food_diary` — diario alimentare famigliare
Diario + pianificazione + dieta per tutta la famiglia. Cross-user: un membro può
loggare per un altro ("Marina ha mangiato…"); default = chi scrive. I membri senza
chat_id Telegram esistono solo come profilo, gestiti da chi li configura.
- **Profili** nutrizionali per membro (`set_diet_profile`/`get_diet_profile`/`delete_profile`):
  età/sesso/altezza/peso/obiettivo/attività/allergie/preferenze; calcola **BMI** e
  **kcal_target** (BMR Mifflin-St Jeor × attività × obiettivo).
- **Peso/BMI**: `log_weight`/`get_weight_history`/`update_weight`/`delete_weight`,
  trend + stats 30gg.
- **Pasti**: `log_meal` (testo, vocale, **foto vision**), `get_meals`, `update_meal`,
  `delete_meal`. Stima grammi/kcal/macro per alimento.
- **Valori nutrizionali reali**: `lookup_nutrition`/`lookup_barcode` →
  **OpenFoodFacts** (barcode/prodotti) + **USDA FoodData Central** (grezzi), fallback Claude,
  cache in `food_cache`.
- **Piano settimanale**: `plan_week`/`get_meal_plan`/`set_plan_meal` (con kcal, override
  per-membro `UNIQUE(date,meal_type,member)`), alternative.
- **Riepilogo giornaliero**: `get_daily_summary` (kcal/macro/acqua vs obiettivo).
- **Idratazione**: `log_hydration`/`get_hydration`/`delete_hydration`.
- **Lista spesa**: `add_shopping_items`/`get_shopping_list`/`check_shopping_item`/
  `remove_shopping_item`/`clear_shopping_list`, prezzi + costo (`set_shopping_price`/
  `get_shopping_cost`), sync verso to-do HA con dedup.
- **Dispensa/scorte** (anti-spreco): `pantry_items` + `update_pantry_item`, alert scadenze.
- **Diete PDF**: `save_diet`/`delete_diet` (file md in `/config/haria_diets`, iniettati
  via `dynamic_prompt`).
- **Notifiche proattive** (scheduler): piano del giorno 08:00, scadenze dispensa 08:30,
  report settimanale dom 20:00.
- **Dashboard**: sensori **MQTT discovery** (broker Mosquitto) per viste HA
  `haria-cibo` (Settimana/Mese/Spesa/Dispensa/Andrea/Marina) + pannello web ingress.

### `bollette` — consumi/costi utenze
Utente manda il **PDF** della bolletta (corrente/acqua/gas); Claude estrae utility, periodo,
consumo, costo → `update_bill` scrive nei CSV mensili riusando script HA
(`script.salva_consumi_*`/`salva_costo_*`) e helper input_select/input_number.
**Dedup**: se il periodo è già registrato ritorna i valori esistenti e chiede conferma
prima di sovrascrivere (`confirm=true`).

> Gli script HA e gli helper usati da questo modulo **non sono inclusi nel repository**
> (sono specifici della propria installazione/dashboard Consumi): vanno creati lato HA e
> possono essere personalizzati a piacimento (nomi entità/script, struttura CSV).

### `web_search` — ricerca web
`search_web` via DuckDuckGo (`ddgs`) per informazioni in tempo reale.

### `multi_user` — messaggi tra membri
`send_message_to_user(member, message)` — HARIA scrive proattivamente su Telegram a un altro
membro della famiglia.

---

## Tool disponibili a Claude

| Tool | Modulo | Cosa fa |
|---|---|---|
| `get_house_state` | core | Lista entità cached, o stato live di entità specifiche |
| `control_device` | core | Chiama un servizio HA (domain/service/data) |
| `get_memory` / `save_memory` | core | Legge/salva note utente (le note sono comunque auto-iniettate) |
| `recall` | core | Ricerca keyword (FTS5) in note + conversazioni passate |
| `speak_alexa` | core | Annuncio TTS su Echo/Alexa (announce→tts fallback) |
| `respond` | core | Risposta testuale all'utente (obbligatorio) |
| `set_reminder` / `list_reminders` / `update_reminder` / `cancel_reminder` | agenda | Promemoria |
| `add_task` / `update_task` / `complete_task` | agenda | To-do su liste HA |
| `add_event` / `get_events` / `update_event` / `delete_event` | agenda | Calendario |
| `agenda_overview` | agenda | Vista unica reminder+task+eventi |
| `set_diet_profile` / `get_diet_profile` / `delete_profile` | food_diary | Profilo + BMI + kcal_target |
| `log_weight` / `get_weight_history` / `update_weight` / `delete_weight` | food_diary | Peso/BMI |
| `log_meal` / `get_meals` / `update_meal` / `delete_meal` | food_diary | Pasti (anche foto) |
| `lookup_nutrition` / `lookup_barcode` | food_diary | Valori reali OFF/USDA |
| `plan_week` / `get_meal_plan` / `set_plan_meal` | food_diary | Piano settimanale |
| `get_daily_summary` | food_diary | Riepilogo kcal/macro/acqua |
| `log_hydration` / `get_hydration` / `delete_hydration` | food_diary | Idratazione |
| `add_shopping_items` / `get_shopping_list` / `check_shopping_item` / `remove_shopping_item` / `clear_shopping_list` | food_diary | Lista spesa |
| `set_shopping_price` / `get_shopping_cost` | food_diary | Costi spesa |
| `update_pantry_item` | food_diary | Dispensa |
| `save_diet` / `delete_diet` | food_diary | Diete da PDF |
| `update_bill` | bollette | Consumi/costi utenze da PDF |
| `search_web` | web_search | Ricerca web |
| `send_message_to_user` | multi_user | Messaggio proattivo a un membro |

---

## Database

SQLite (`aiosqlite`) in `/config/haria.db`, schema gestito in `memory.py:init_db()`
(con migrazioni leggere). Tabelle principali:

| Tabella | Scopo |
|---|---|
| `conversations` | History conversazione per utente |
| `conv_summary` | Riassunto a lungo termine per utente |
| `memory_fts` | FTS5 — recall keyword su note + conversazioni |
| `notes` | Note/preferenze utente (key-value, auto-iniettate) |
| `entity_cache` | Cache lista entità HA (id 1) |
| `reminders` | Promemoria one-shot/ricorrenti |
| `diet_profiles` | Profili nutrizionali per membro |
| `weight_log` | Storico peso + BMI |
| `meals` / `meal_items` | Pasti + dettaglio per alimento |
| `meal_plan` | Piano settimanale (override per-membro) |
| `hydration_log` | Idratazione |
| `shopping_items` | Lista spesa (con prezzo) |
| `pantry_items` | Dispensa/scorte |
| `food_cache` | Cache valori nutrizionali OFF/USDA |

---

## Configurazione

`config.py` legge `/config/haria_options.json` (priorità) → fallback `/data/options.json`,
cache in RAM (`reload()` per rileggere; comando `/reloadconfig`).

> **Gotcha**: ogni nuovo modulo va aggiunto anche a `/config/haria_options.json`, non basta
> `config.yaml`/UI addon. Le **chiavi `modules`** sono i `NAME` dei moduli
> (`web_search`, `multi_user`, non i filename).

```yaml
anthropic_key: "sk-ant-..."
telegram_token: "..."
groq_key: "gsk_..."          # trascrizione vocale (opzionale)
fdc_key: "..."               # USDA FoodData Central (opzionale, default DEMO_KEY)
ha_url: "http://homeassistant.local:8123"
ha_token: "..."              # Long-lived access token HA

modules:
  telegram: true
  ha_chat: false
  voice: true
  agenda: true
  food_diary: true
  web_search: true
  multi_user: true
  bollette: true

users:
  - name: "Andrea"
    chat_id: "XXXXXXXXXX"
    context: "Sono Andrea, vivo a [città]. Mia moglie si chiama Marina."
  - name: "Marina"
    chat_id: "XXXXXXXXXX"
    context: "Sono Marina, moglie di Andrea."
```

> **Sicurezza**: i token sono in chiaro in `haria_options.json` — non committare il file.
> `.claude/settings.local.json` è gitignorato.

---

## Deploy

Addon Docker (Python 3.12-alpine), slug `ab6b3a45_haria`, `boot:auto`. Pipeline:

1. `git push origin main`
2. `shell_command.haria_supervisor_repair` (git pull addon store)
3. `homeassistant.reload_config_entry` su `update.haria_update`
4. `update.install` su `update.haria_update`

La versione è in `haria/config.yaml` (`version:`), va bumpata a ogni release.

---

## Stack tecnologico

| Componente | Tecnologia |
|---|---|
| Linguaggio | Python 3.12 (alpine) |
| AI | Anthropic Claude (`claude-haiku-4-5`) |
| Trascrizione vocale | Groq Whisper (`whisper-large-v3-turbo`) |
| Telegram | python-telegram-bot (polling) |
| Database | SQLite (aiosqlite) + FTS5 |
| Scheduler | APScheduler |
| Dashboard food | MQTT discovery (paho-mqtt) + Mosquitto |
| Web panel | aiohttp + HTML/CSS/JS vanilla (ingress) |
| Barcode | pyzbar + Pillow (zbar) |
| Nutrizione | OpenFoodFacts + USDA FoodData Central |
| Container | Docker (addon HA) |

---

## Roadmap / residui

**Sicurezza / robustezza**
- Allowlist `control_device` (oggi Claude può chiamare qualsiasi servizio HA).
- Test automatici (oggi solo `py_compile`).
- TTL su `food_cache`.

**Food**
- Micronutrienti (fibre, zuccheri, saturi, sodio, vitamine/minerali) — schema previsto, non popolato.
- Controllo allergie hard (oggi solo via ragionamento prompt).
- Pannello web interattivo (oggi sola-lettura): check-off spesa, edit profili, grafici.

**Piattaforma**
- `ha_chat` → conversation agent nativo HA (Assist pipeline).
- Modulo `home_economics` (spese/budget/entrate, ricorrenti, report) — ponte con `food_diary`/`bollette`.
- Briefing mattutino unificato (meteo + agenda + todo + scadenze).
- Integrazione email (Gmail), reply vocale TTS su Telegram.
- Pubblicazione HACS (`hacs.json`, metadati).
- Companion app Android (progetto a sé).
