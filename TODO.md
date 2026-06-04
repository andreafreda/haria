# HARIA — TODO (livello codice)

Stato: v0.1.27. Area food completa nel core. Elenco lavori residui a livello codice.

## Food — integrazioni mancanti

- [ ] **Sync spesa → To-do HA non testato**
  - `modules/food_diary.py` → tool `sync_shopping_to_ha` cerca entità `todo.*` e chiama `todo.add_item`.
  - Nessuna entità `todo.*` esiste ancora in HA → mai eseguito davvero.
  - TODO: gestire caso "nessuna lista todo" con messaggio chiaro; permettere scelta lista target (config o param); evitare duplicati (check item già presente prima di add).

- [ ] **Storia peso reale**
  - `mqtt_pub.refresh()` pubblica `peso` = snapshot `profile.weight_kg` (flat finché non cambia).
  - `memory.get_weight_history()` esiste ma non alimenta MQTT.
  - TODO: valutare sensor che espone ultimo peso loggato + statistica varianza (min/max/delta periodo).

- [ ] **Macro target nel webpanel ingress**
  - `webpanel.py` pagina Profili/Diario mostra macro consumati, NON i target.
  - `compute_macro_targets()` già disponibile.
  - TODO: aggiungere colonne/righe target + delta in `_h_profiles` / `_h_diary`.

## Food — feature backlog

- [ ] **Dispensa / scorte (anti-spreco)**
  - Nuovo modulo `modules/pantry.py`: tabella `pantry_items` (nome, qty, scadenza), tool add/list/consume/expiring.
  - Integrazione: a fine spesa → carico dispensa; piano pasti → scala scorte; alert scadenze (scheduler).

- [ ] **Budget / costi spesa (home_economics)**
  - Prezzo per voce spesa, totale settimanale/mensile, sensor MQTT `spesa_costo`.
  - Schema: aggiungere `price` a `shopping_items`; report nel weekly scheduler.

- [ ] **Foto codice a barre**
  - `telegram_handler` già gestisce foto (vision). Aggiungere: foto barcode → decode → `lookup_barcode` esistente.
  - TODO: libreria decode barcode (pyzbar/zxing) in requirements; branch in handler per immagini barcode.

## Moduli / piattaforma

- [ ] **ha_chat module**
  - Modulo disattivo (`config.yaml modules.ha_chat: false`). Verificare implementazione/handler conversazione lato HA.

- [ ] **Rimozione secret dal repo (BLOCCANTE per pubblicazione HACS)**
  - Prima di rendere pubblico/HACS: togliere chiavi/token da config, history, eventuali file committati.
  - Audit: `config.yaml` options vuote (ok), verificare nessun secret in commit passati.

- [ ] **App Android** (fuori scope addon — client separato)

## Note tecniche

- Entità food sono MQTT discovery via broker Mosquitto (`core-mosquitto`). Richiede broker attivo + integrazione MQTT.
- `meal_plan` ora supporta override per-membro: `UNIQUE(date, meal_type, member)`, `member=''` = comune.
- Dashboard HA: `haria-cibo` (Settimana / Mese / Spesa / Andrea / Marina).
