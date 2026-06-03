# HARIA — Home Assistant Reactive Intelligent Agent

## Cos'è HARIA

HARIA è un addon per Home Assistant che integra un agente AI personale basato su Claude (Anthropic). Permette di interagire con la propria casa, ricevere risposte intelligenti e gestire la quotidianità tramite Telegram e una chat integrata in Home Assistant.

Il nome è un acronimo: **H**ome **A**ssistant **R**eactive **I**ntelligent **A**gent. Si pronuncia "Aria".

---

## Obiettivi del progetto

- Creare un agente AI personale accessibile via Telegram e chat HA
- Integrare il controllo della casa (luci, allarme, sensori) tramite linguaggio naturale
- Mantenere memoria conversazionale persistente per ogni utente
- Supportare messaggi vocali tramite trascrizione Groq Whisper
- Essere modulare: ogni feature si attiva/disattiva dalla configurazione
- Essere distribuibile su HACS per la community Home Assistant

---

## Requisiti

### Must Have

| # | Requisito | Descrizione |
|---|---|---|
| 1 | Assistente generale | Risponde a domande, curiosità, calcoli — usa la conoscenza interna di Claude |
| 2 | Controllo casa | Controlla luci, allarme, termostati e dispositivi HA via linguaggio naturale |
| 3 | Stato casa | Risponde a "com'è la situazione a casa?" con temperatura, stati, sensori |
| 4 | Contesto personale | Claude conosce gli utenti, la famiglia, le abitudini — prompt di sistema personalizzato |
| 5 | Memoria conversazionale | Ricorda le conversazioni precedenti per ogni utente tramite SQLite |
| 6 | Multi-utente | Ogni utente ha contesto e memoria separati, configurati tramite chat_id Telegram |
| 7 | Messaggi vocali | Supporto audio Telegram via Groq Whisper API (trascrizione veloce e gratuita) |

### Nice to Have

| # | Requisito | Descrizione |
|---|---|---|
| N1 | Promemoria one-shot | "Ricordami alle 10 di chiamare il medico" |
| N2 | Promemoria ricorrenti | "Ogni lunedì mandami un riepilogo della settimana" |
| N3 | Liste della spesa / todo | Gestione liste collegate alle todo list di HA |
| N4 | Diario alimentare | Log pasti con stima calorie tramite Claude |
| N5 | Notizie dal web | Ricerca informazioni in tempo reale (DuckDuckGo API) |
| N6 | Pubblicazione HACS | Distribuzione ufficiale sulla community Home Assistant |
| N7 | Companion app Android | App che sostituisce Google Assistant sul telefono |

---

## Architettura

### Flusso generale

```
UTENTE (Telegram / Chat HA)
        │
        │ testo o vocale
        ▼
┌─────────────────────────────────────────┐
│              HARIA Core                 │
│                                         │
│  ┌──────────┐    ┌───────────────────┐  │
│  │ Telegram │───▶│   Router utente   │  │
│  │ Listener │    │  (chi sta scrive?)│  │
│  └──────────┘    └────────┬──────────┘  │
│                           │             │
│              ┌────────────▼──────────┐  │
│              │     Claude Engine     │  │
│              │    (tool calling)     │  │
│              └────────────┬──────────┘  │
│                           │             │
│     ┌─────────────────────┼─────────────┤
│     │                     │             │
│  ┌──▼──────┐    ┌─────────▼──┐  ┌──────▼───┐
│  │  Casa   │    │  Memoria   │  │Scheduler │
│  │  Tool   │    │  SQLite    │  │Reminder  │
│  └──┬──────┘    └────────────┘  └──────────┘
│     │                                        
└─────┼────────────────────────────────────────┘
      │
      ▼
 HA REST API
```

### Flusso vocale

```
Audio Telegram → Groq Whisper API → testo → Claude → risposta → Telegram
```

### Flusso testo

```
Testo → Claude → sceglie tool → esegue → risposta → Telegram
```

---

## Tool disponibili per Claude

| Tool | Modulo | Descrizione |
|---|---|---|
| `get_house_state` | core | Legge stati entità da HA |
| `control_device` | core | Controlla dispositivi HA |
| `get_memory` | core | Recupera note e conversazioni passate |
| `save_memory` | core | Salva note e preferenze utente |
| `send_message_to_user` | multi_user | Manda messaggio a un altro utente |
| `set_reminder` | reminders | Crea promemoria one-shot o ricorrente |
| `manage_todo` | todo | Gestisce liste della spesa e todo |
| `log_meal` | food_diary | Registra pasto con stima calorie |
| `search_web` | web_search | Cerca informazioni in tempo reale |

---

## Struttura file addon

```
haria/
├── HARIA.md                  # questo documento
├── hacs.json                 # metadati HACS
├── haria/
│   ├── config.yaml           # schema configurazione addon
│   ├── Dockerfile
│   ├── run.sh
│   └── app/
│       ├── main.py           # entry point
│       ├── telegram.py       # gestione Telegram webhook
│       ├── claude.py         # Claude Engine + tool calling
│       ├── ha_client.py      # client REST per HA
│       ├── memory.py         # SQLite — memoria conversazionale
│       ├── scheduler.py      # promemoria e task ricorrenti
│       ├── voice.py          # trascrizione audio Groq
│       ├── modules/          # moduli opzionali
│       │   ├── reminders.py
│       │   ├── todo.py
│       │   ├── food_diary.py
│       │   └── web_search.py
│       └── web/              # chat panel HA
│           ├── index.html
│           ├── style.css
│           └── app.js
└── database/
    └── schema.sql            # struttura SQLite
```

---

## Configurazione addon

```yaml
anthropic_key: "sk-ant-..."
telegram_token: "..."
groq_key: "gsk_..."           # per trascrizione vocale
ha_url: "http://homeassistant.local:8123"
ha_token: "..."               # Long-lived access token HA

modules:
  telegram: true
  ha_chat: true
  voice: true
  reminders: false
  todo: false
  food_diary: false
  web_search: false
  multi_user: true

users:
  - name: "Andrea"
    chat_id: "XXXXXXXXXX"
    context: "Sono Andrea, vivo a [città]. Ho una moglie che si chiama Marina."
  - name: "Marina"
    chat_id: "XXXXXXXXXX"
    context: "Sono Marina, moglie di Andrea."
```

---

## Database SQLite — Schema

```sql
-- Memoria conversazionale
CREATE TABLE conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,          -- 'user' o 'assistant'
    content TEXT NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Promemoria
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    message TEXT NOT NULL,
    remind_at DATETIME,
    recurring TEXT,              -- es. 'every monday 09:00'
    active INTEGER DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Diario alimentare
CREATE TABLE meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    description TEXT NOT NULL,
    calories INTEGER,
    meal_type TEXT,              -- colazione, pranzo, cena, spuntino
    logged_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Todo / liste spesa
CREATE TABLE todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    list_name TEXT DEFAULT 'spesa',
    item TEXT NOT NULL,
    done INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

---

## Stack tecnologico

| Componente | Tecnologia |
|---|---|
| Linguaggio | Python 3.11+ |
| AI | Anthropic Claude API (claude-haiku-3-5) |
| Trascrizione vocale | Groq Whisper API |
| Telegram | python-telegram-bot |
| Database | SQLite (aiosqlite) |
| Scheduler | APScheduler |
| Web panel | HTML/CSS/JS vanilla |
| Container | Docker (addon HA) |

---

## Step di sviluppo

### Fase 1 — Core (MVP)
- [x] Struttura repo e Dockerfile addon HA
- [x] Configurazione yaml e schema
- [x] Telegram listener (testo)
- [x] Integrazione Claude con tool calling base
- [x] Tool `get_house_state` e `control_device` via HA REST API
- [x] Memoria conversazionale SQLite
- [x] Multi-utente con contesti separati
- [ ] Deploy e test su HA reale

### Fase 2 — Voce
- [x] Ricezione audio da Telegram (`telegram_voice`)
- [x] Download file audio da Telegram API
- [x] Trascrizione via Groq Whisper API
- [x] Integrazione nel flusso principale

### Fase 3 — Chat HA
- [ ] Web panel minimale (HTML/CSS/JS)
- [ ] Endpoint REST interno addon
- [ ] Autenticazione via HA token

### Fase 4 — Nice to have
- [ ] Modulo reminders (APScheduler)
- [ ] Modulo todo (HA todo list)
- [ ] Modulo food_diary
- [ ] Modulo web_search (DuckDuckGo)

### Fase 5 — Community
- [ ] hacs.json e metadati
- [ ] README utente finale
- [ ] Pubblicazione HACS

---

## Note per reset agente

Questo documento è la memoria del progetto. In caso di reset:

1. Il progetto si chiama **HARIA** — Home Assistant Reactive Intelligent Agent
2. È un addon HA custom scritto in Python
3. Usa **Claude** (Anthropic) come AI, **Groq Whisper** per i vocali, **Telegram** come interfaccia principale
4. L'architettura è modulare — ogni feature è un modulo on/off
5. L'integrazione Telegram nativa di HA va **disattivata** quando HARIA è attivo (conflitto bot token)
6. Il database è SQLite locale nell'addon
7. Gli utenti si configurano con nome + chat_id Telegram + contesto personale
8. Il repo è https://github.com/andreafreda/haria
