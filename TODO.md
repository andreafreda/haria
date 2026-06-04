# HARIA — TODO (livello codice)

Stato: v0.1.31. Area food completa nel core. Elenco lavori residui a livello codice.

## Food — fatto (v0.1.28)

- [x] **Sync spesa → To-do HA dedup** — `sync_shopping_to_ha` ora chiama `todo.get_items`, salta item già presenti, ritorna `{ok,list,added,skipped}`. Manca solo: nessuna entità `todo.*` esiste in HA (azione utente: installare integrazione To-do/Shopping List).
- [x] **Macro target webpanel** — `_h_diary` riga "vs target", `_h_profiles` colonne Prot./Carbo/Grassi target via `compute_macro_targets()`. Pagina `/pantry` + nav.
- [x] **Dispensa / scorte (anti-spreco)** — tabella `pantry_items` + 5 funzioni in `memory.py`; 4 tool in `food_diary.py`; sensor MQTT `dispensa` + `dispensa_in_scadenza`; alert scheduler 08:30; vista HA "Dispensa".

- [x] **Storia peso reale (v0.1.29)** — `memory.get_weight_stats(member,days)`; `mqtt_pub` pubblica peso/bmi da ultimo log reale + sensor `peso_delta_30d` / `peso_min_30d` / `peso_max_30d`.

## Food — feature backlog

- [ ] **Budget / costi spesa (home_economics)**
  - Prezzo per voce spesa, totale settimanale/mensile, sensor MQTT `spesa_costo`.
  - Schema: aggiungere `price` a `shopping_items`; report nel weekly scheduler.

- [x] **Foto codice a barre (v0.1.32)** — `telegram_handler._decode_barcode` (pyzbar+Pillow) decodifica EAN/UPC; se trovato passa hint a `chat` → `lookup_barcode` (OFF) per dati reali. Dockerfile: `apk add zbar jpeg-dev zlib-dev`.

## Moduli / piattaforma

- [x] **ha_chat (v0.1.30)** — chat web in `webpanel.py`: pagina `/chat` (UI JS) + `POST /api/chat` → `claude_engine.chat`. Nav "Chat" + vista HA "Chat" (iframe ingress). `web_server.py` morto rimosso.
  - TODO opzionale: integrazione conversation agent nativo HA (Assist pipeline) — più complesso, non fatto.

- [ ] **Rimozione secret dal repo (BLOCCANTE per pubblicazione HACS)**
  - Prima di rendere pubblico/HACS: togliere chiavi/token da config, history, eventuali file committati.
  - Audit: `config.yaml` options vuote (ok), verificare nessun secret in commit passati.

- [ ] **App Android** (fuori scope addon — client separato)

## Note tecniche

- Entità food sono MQTT discovery via broker Mosquitto (`core-mosquitto`). Richiede broker attivo + integrazione MQTT.
- `meal_plan` ora supporta override per-membro: `UNIQUE(date, meal_type, member)`, `member=''` = comune.
- Dashboard HA: `haria-cibo` (Settimana / Mese / Spesa / Dispensa / Andrea / Marina / Chat).
