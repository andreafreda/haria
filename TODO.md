# HARIA — TODO (livello codice)

Stato: v0.1.45. Agenda unificata + import bollette PDF con dedup. Area food completa. Lavori residui sotto.

## Bollette PDF → dashboard Consumi — fatto (v0.1.44→v0.1.45)

- [x] **Modulo `bollette`** — tool `update_bill(utility, year, month_start, month_end?, consumo?, costo?)`. Utente manda PDF al bot; Claude legge il documento (già passato come doc_b64) ed estrae dati; update_bill riusa gli script HA `script.salva_consumi_<u>`/`script.salva_costo_<u>` settando i helper input_select/input_number. Toggle `modules.bollette`.
- [x] **Dedup (v0.1.45)** — prima di scrivere, `update_bill` legge `input_text.csv_<u>_<metric>_<year>` (metric: corrente=kwh, acqua/gas=m3, costo) e controlla gli slot dei mesi target. Se ≠0 ritorna `{ok:false, gia_registrato:true, esistente:{...}}` senza scrivere; Claude mostra vecchio vs nuovo e chiede conferma → richiama con `confirm=true` per sovrascrivere.

## Agenda unificata — fatto (v0.1.43)

- [x] **Modulo `agenda`** — unisce `reminders`+`todo`+calendar in un solo modulo/toggle. Rimossi `reminders`/`todo` da ALL (file orfani restano). `config.yaml modules.agenda`.
- [x] **Calendar HA** — creati `calendar.haria_andrea`/`calendar.haria_marina` (local_calendar via config-flow REST). `add_event`/`get_events`: multi-owner = evento su ogni calendario owner; nessun owner = tutti (famiglia).
- [x] **Multi-owner task** — `add_task` accetta `owners[]` (lista), folded in description "👤 a, b".
- [x] **`agenda_overview`** — vista unica reminder+task aperti+eventi prossimi → fine desync tra i 3 mondi.
- [x] **Dashboard `haria-agenda`** — view condivisa: calendar card (settimana+mese, entrambi calendari) + todo card (cose da fare/promemoria/spesa).

## Diario alimentare — fatto (v0.1.37→v0.1.41)

- [x] **Storico diario webpanel (v0.1.37)** — `_h_diary`: nav date prev/oggi/next via `?date=`, sezione "Storico" ultimi 30gg con pasti registrati (`memory.get_logged_days`).
- [x] **delete_meal (v0.1.38)** — `memory.delete_meal(meal_id)` (+meal_items); tool `delete_meal` in food_diary; bot cancella pasti registrati.
- [x] **update_meal (v0.1.39)** — `memory.update_meal(meal_id,...)` UPDATE parziale; tool `update_meal`; bot corregge pasti.
- [x] **Fix get_meals date filter (v0.1.40)** — `eaten_at` salvato con spazio (CURRENT_TIMESTAMP); confronto `>= "...T00:00:00"` escludeva tutto (spazio < `T`). Ora `DATE(eaten_at)`.
- [x] **Diario settimana dashboard (v0.1.41)** — sensor MQTT `haria_<m>_diario_settimana` (pasti reali lun-dom, attr per giorno); card "Diario settimana" viste Andrea/Marina.

## Food — fatto (v0.1.28)

- [x] **Sync spesa → To-do HA dedup** — `sync_shopping_to_ha` ora chiama `todo.get_items`, salta item già presenti, ritorna `{ok,list,added,skipped}`. Manca solo: nessuna entità `todo.*` esiste in HA (azione utente: installare integrazione To-do/Shopping List).
- [x] **Macro target webpanel** — `_h_diary` riga "vs target", `_h_profiles` colonne Prot./Carbo/Grassi target via `compute_macro_targets()`. Pagina `/pantry` + nav.
- [x] **Dispensa / scorte (anti-spreco)** — tabella `pantry_items` + 5 funzioni in `memory.py`; 4 tool in `food_diary.py`; sensor MQTT `dispensa` + `dispensa_in_scadenza`; alert scheduler 08:30; vista HA "Dispensa".

- [x] **Storia peso reale (v0.1.29)** — `memory.get_weight_stats(member,days)`; `mqtt_pub` pubblica peso/bmi da ultimo log reale + sensor `peso_delta_30d` / `peso_min_30d` / `peso_max_30d`.

## Food — feature backlog

- [x] **Budget / costi spesa (v0.1.36)** — colonna `price` su `shopping_items` (migrazione); `set_shopping_price`/`get_shopping_cost` in memory.py; tool `set_shopping_price`+`get_shopping_cost` e `price` in `add_shopping_items` (food_diary); sensor MQTT `spesa_costo` (+attr voci con/senza prezzo); totale+prezzi nella vista Spesa webpanel; riga costo nel report settimanale.

- [x] **Foto codice a barre (v0.1.32)** — `telegram_handler._decode_barcode` (pyzbar+Pillow) decodifica EAN/UPC; se trovato passa hint a `chat` → `lookup_barcode` (OFF) per dati reali. Dockerfile: `apk add zbar jpeg-dev zlib-dev`.

## Moduli / piattaforma

- [x] **ha_chat (v0.1.30)** — chat web in `webpanel.py`: pagina `/chat` (UI JS) + `POST /api/chat` → `claude_engine.chat`. Nav "Chat" + vista HA "Chat" (iframe ingress). `web_server.py` morto rimosso.
  - TODO opzionale: integrazione conversation agent nativo HA (Assist pipeline) — più complesso, non fatto.

- [x] **Rimozione secret dal repo (v0.1.32)** — `.claude/settings.local.json` (HA JWT + Telegram token) rimosso da working tree + `.gitignore`, purgato da TUTTA la history via `git filter-repo --invert-paths`, force-push `main`. Verificato: file e token assenti da `git log --all`. NB: token NON ruotati (scelta utente) — restano validi e potenzialmente in cache GitHub / cloni esistenti.

- [ ] **App Android** (fuori scope addon — client separato)

## Residui aperti

- [ ] **Ruotare token** (HA JWT + Telegram) prima di pubblicare repo. Differito da utente. Token attuali ancora validi/esposti in cache GitHub / cloni.
- [ ] **Integrazione To-do/Shopping List HA** (azione UTENTE) — 0 entità `todo.*`; `sync_shopping_to_ha` pronto ma senza target.
- [ ] **ha_chat → conversation agent nativo (Assist pipeline)** — opzionale, più complesso.

## Note tecniche

- Entità food sono MQTT discovery via broker Mosquitto (`core-mosquitto`). Richiede broker attivo + integrazione MQTT.
- `meal_plan` ora supporta override per-membro: `UNIQUE(date, meal_type, member)`, `member=''` = comune.
- Dashboard HA: `haria-cibo` (Settimana / Mese / Spesa / Dispensa / Andrea / Marina). Viste Andrea/Marina includono "Diario settimana".
