# HARIA — Dev Log

Decisioni tecniche, file creati, note implementative. Aggiornato a ogni sessione di sviluppo.

---

## 2026-06-03 — Fase 1 + 2: MVP Core + Voce

### File creati

| File | Scopo |
|---|---|
| `haria/run.sh` | Script avvio addon — legge config HA con bashio, esporta env vars |
| `haria/app/requirements.txt` | Dipendenze Python: anthropic, python-telegram-bot, aiosqlite, aiohttp, apscheduler, groq |
| `haria/app/main.py` | Entry point — init DB, avvia polling Telegram |
| `haria/app/telegram_handler.py` | Handler messaggi testo e vocali Telegram |
| `haria/app/claude_engine.py` | Claude Engine con tool calling (agentic loop) |
| `haria/app/ha_client.py` | Client REST asincrono per HA API |
| `haria/app/memory.py` | SQLite asincrono — salva/carica storico conversazioni per utente |
| `database/schema.sql` | DDL SQLite: conversations, reminders, meals, todos |

### Decisioni tecniche

- **Modello AI**: `claude-haiku-3-5-20241022` — veloce, economico, adatto a risposte in tempo reale su Telegram
- **Polling Telegram**: usato `drop_pending_updates=True` per ignorare messaggi arrivati mentre l'addon era spento
- **Tool calling loop**: agentic loop in `claude_engine.py` — continua finché `stop_reason != tool_use`
- **Memoria**: ultimi 20 messaggi per utente (costante `MAX_HISTORY`), recuperati in ordine cronologico
- **Auth utenti**: whitelist chat_id in `options.json` — chi non è in lista riceve rifiuto esplicito
- **Voce**: trascrizione Groq Whisper `whisper-large-v3-turbo`, lingua IT forzata; risposta mostra trascrizione in corsivo + risposta Claude
- **Config path**: `/data/options.json` — percorso standard addon HA per opzioni utente

### Tool disponibili (Fase 1)

| Tool | Implementato | Note |
|---|---|---|
| `get_house_state` | ✅ | Accetta lista entity_ids o tutte |
| `control_device` | ✅ | domain + service + data libero |
| `get_memory` | ❌ | Fase futura |
| `save_memory` | ❌ | Fase futura |
| `set_reminder` | ❌ | Fase 4 |
| `manage_todo` | ❌ | Fase 4 |
| `log_meal` | ❌ | Fase 4 |
| `search_web` | ❌ | Fase 4 |

### Ancora da fare

- [ ] Deploy e test su HA reale
- [ ] Verifica bashio disponibile nell'immagine base (dipende da `BUILD_FROM`)
- [x] Gestione errori HA API (timeout, token scaduto)
- [ ] Test multi-utente reale con due chat_id diversi

---

## 2026-06-03 — Robustezza + tool memoria

### File modificati

| File | Modifica |
|---|---|
| `ha_client.py` | Timeout 10s, gestione 401, `ClientConnectorError`, `TimeoutError` con messaggi chiari |
| `memory.py` | Aggiunta tabella `notes` (key/value per utente), funzioni `get_notes` / `save_note` con upsert |
| `claude_engine.py` | Aggiunti tool `get_memory` e `save_memory`; error handling in `_run_tool`; `user_id` passato al tool runner |
| `telegram_handler.py` | Aggiunto handler `/start` — mostra chat_id se utente non autorizzato |

### Decisioni tecniche

- `notes` usa `UNIQUE(user_id, key)` + `ON CONFLICT DO UPDATE` — upsert senza duplicati
- Errori tool HA tornano come stringa a Claude (non eccezione) — Claude può rispondere all'utente con messaggio sensato
- `/start` mostra chat_id se non autorizzato — facilita configurazione addon

### Tool disponibili (aggiornato)

| Tool | Implementato |
|---|---|
| `get_house_state` | ✅ |
| `control_device` | ✅ |
| `get_memory` | ✅ |
| `save_memory` | ✅ |
| `set_reminder` | ❌ Fase 4 |
| `manage_todo` | ❌ Fase 4 |
| `log_meal` | ❌ Fase 4 |
| `search_web` | ❌ Fase 4 |

### Ancora da fare

- [ ] Deploy e test su HA reale
- [ ] Verifica bashio disponibile nell'immagine base
- [ ] Test multi-utente con due chat_id diversi

---

## Ambiente HA reale

| Parametro | Valore |
|---|---|
| HA OS | 17.3, core 2026.6.0b2 |
| Hardware | VM KVM, 4GB RAM |
| URL locale | http://homeassistant.local:8123 |
| URL esterno | https://ha.andreafreda.cloud |
| Utente Andrea | chat_id `210413540`, notify `notify.mobile_app_rmx3301` |
| Utente Marina | chat_id `799761342`, notify `notify.mobile_app_sm_s911b` |

### Telegram bot handover (automatico)

HARIA gestisce il conflitto col bot HA in modo automatico e non distruttivo:

**All'avvio (`_acquire_bot`)**:
1. Legge webhook esistente con `getWebhookInfo` e lo salva in memoria
2. Cancella webhook con `deleteWebhook` (senza droppare i messaggi pendenti)
3. Avvia polling — HARIA prende controllo esclusivo del bot

**Allo stop (`_release_bot`)**:
1. Se c'era un webhook precedente, lo ripristina con `setWebhook` (incluso `secret_token` se presente)
2. L'integrazione HA Telegram riprende normalmente al prossimo restart

**Risultato**: nessuna modifica manuale necessaria. Se HARIA non parte, il webhook HA resta intatto.

---

## Template prossime sessioni

```
## YYYY-MM-DD — [Fase X: titolo]

### File creati/modificati
### Decisioni tecniche
### Bug risolti
### Ancora da fare
```
