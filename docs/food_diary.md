# HARIA — Diario Alimentare Famigliare

Modulo `food_diary`. Va oltre il semplice log: diario + pianificazione + dieta per tutta la famiglia, con consigli su cosa cucinare e spesa semplificata.

## Membri famiglia

Riusa `users[]` esistente (name + chat_id). Ogni membro ha un profilo nutrizionale:

- Andrea, Marina, bimba (e altri futuri)
- Profilo per membro: età, peso, obiettivi (mantenere/dimagrire), allergie/intolleranze, preferenze (ama/odia), restrizioni (es. cibi per bimba)

## Feature

### Fase 1 — Log pasti (base)

- [ ] Log pasto via Telegram, testo o vocale ("ho mangiato pasta al pomodoro")
- [ ] HARIA struttura: membro, tipo pasto (colazione/pranzo/cena/snack), alimenti, porzione, data/ora
- [ ] Multi-utente: ogni membro logga i propri pasti; bimba loggata da un genitore
- [ ] Query: "cosa ho mangiato oggi?", "cosa ha mangiato la bimba ieri?"
- [ ] Storage SQLite (nuova tabella `meals`)

### Fase 2 — Profili e dieta

- [ ] Profilo nutrizionale per membro (età, sesso, altezza, peso, obiettivi, allergie, preferenze)
- [ ] Stima nutrizionale dei pasti (calorie + macro) — via Claude
- [ ] Controllo allergie/restrizioni: avviso se un pasto contiene allergeni del membro
- [ ] Riepilogo giornaliero per membro (totale calorie/macro vs obiettivo)
- [ ] **Tracking peso**: log peso nel tempo via Telegram ("oggi peso 78kg"), storico per membro
- [ ] **BMI**: calcolo automatico da peso+altezza, categoria (sottopeso/normale/sovrappeso), trend nel tempo
- [ ] **Fabbisogno calorico**: stima kcal_target da profilo (BMR Mifflin-St Jeor + livello attività + obiettivo)
- [ ] Per la bimba: percentili crescita invece di BMI adulto (peso/altezza per età)

### Fase 3 — Pianificazione settimanale

- [ ] Piano pasti settimanale per tutta la famiglia (colazione/pranzo/cena × 7 giorni)
- [ ] HARIA propone il menù tenendo conto di: obiettivi dieta, allergie, preferenze, varietà (no ripetizioni), cosa già mangiato
- [ ] Piano modificabile a voce ("cambia la cena di giovedì")
- [ ] Vista piano: "cosa si mangia questa settimana?"

### Fase 4 — Consigli e semplificazione

- [ ] "Cosa cucino stasera?" → suggerimento basato su piano + cosa avanzato + ingredienti disponibili
- [ ] Ricette semplici associate ai pasti pianificati
- [ ] Adatta porzioni al numero di persone presenti

### Fase 5 — Spesa

- [ ] Lista spesa generata dal piano settimanale (aggrega ingredienti)
- [ ] Sottrae ciò che è già in dispensa (dispensa tracciata o chiesta)
- [ ] Integrazione con modulo `todo` per la lista
- [ ] "Cosa manca da comprare?"

### Fase 6 — Proattività e tracciamento

- [ ] Report settimanale automatico via Telegram (scheduler esistente): cosa mangiato, aderenza dieta, suggerimenti
- [ ] Promemoria pasti/spesa (riusa modulo `reminders`)
- [ ] Annunci Alexa via `speak_alexa` ("la cena è pasta al forno", "ricordati di scongelare il pollo")
- [ ] Storico e trend nel tempo per membro

### Fase 7 — Logging avanzato e nutrizione di dettaglio

Ispirato a app 2026 (Ollie, Mealime, Cronometer, Eat This Much, Yazio).

- [ ] **Logging foto**: scatti foto del piatto via Telegram → Claude (vision) riconosce alimenti e stima grammi/calorie
- [ ] **Barcode**: foto del codice a barre prodotto → lookup OpenFoodFacts per valori esatti da etichetta
- [ ] **Database alimenti**: porzioni standard predefinite (1 uovo ≈ 60g, 1 piatto pasta ≈ 80g secca) per stime coerenti
- [ ] **Micronutrienti**: oltre i 3 macro, traccia fibre, zuccheri, grassi saturi, sodio, e principali vitamine/minerali (ferro, calcio, vit. D) — utile per dieta bimba
- [ ] **Idratazione**: tracking acqua/liquidi giornalieri per membro
- [ ] **Swap family-friendly**: sostituzioni rapide nel piano quando gusti diversi (es. bimba mangia altro)
- [ ] **Budget spesa**: stima costo lista spesa, controllo tetto settimanale
- [ ] **Aderenza/trend**: % obiettivo dieta raggiunto, streak, grafici settimana/mese per membro

### Fase 8 — Pannello web HA (ingress)

HARIA ha già ingress (porta 8099, voce in sidebar HA). Si serve un'interfaccia web dedicata dentro Home Assistant — stessi dati del bot Telegram (stesso SQLite). Backend Python/aiohttp già presente; si aggiungono route HTTP + frontend statico.

Schermate:

- [ ] **Piano settimana**: griglia 7 giorni × pasti (colazione/pranzo/cena), cosa cucinare, editabile, swap rapidi
- [ ] **Diario persona**: log pasti per singolo membro (Andrea / Marina / bimba), filtro per data, dettaglio grammi/kcal/macro
- [ ] **Nutrizione**: kcal e macro giornalieri per membro vs obiettivo, grafici settimana/mese, aderenza
- [ ] **Peso / BMI**: trend peso per membro, BMI e categoria, percentili crescita per la bimba
- [ ] **Lista spesa**: generata dal piano, check-off interattivo, stima costo
- [ ] **Profili**: modifica età, sesso, altezza, obiettivi, allergie, preferenze per membro

Note tecniche:
- API JSON interne (aiohttp) leggono/scrivono lo stesso DB usato da Telegram
- Frontend leggero (HTML + HTMX o vanilla JS), no build step pesante
- Telegram (input rapido/vocale) e pannello (vista/editing strutturato) = due facce stessi dati
- Lega al modulo `ha_chat` esistente (flag già in config, ora off)

## Dettaglio nutrizionale (grammi / calorie)

Ogni pasto loggato salva valori quantitativi, non solo testo:

- **Porzione in grammi** per ogni alimento (es. "pasta 80g, pomodoro 100g, olio 10g")
- **Calorie totali** pasto (kcal) + per alimento
- **Macro in grammi**: proteine, carboidrati, grassi
- **Micro** (fase 7): fibre, zuccheri, grassi saturi, sodio, vitamine/minerali chiave
- Aggregazione giornaliera per membro: totale kcal + macro vs obiettivo profilo
- Fonte stima tracciata: `openfoodfacts` | `usda` | `claude` (fallback) | `manual` (utente corregge grammi)

Flusso stima:
1. Utente descrive ("ho mangiato un piatto di pasta al pomodoro") o scansiona barcode
2. Risoluzione valori per ogni alimento, in ordine di priorità:
   - **Barcode** → OpenFoodFacts (prodotto confezionato, etichetta reale, copertura IT)
   - **Alimento grezzo/cucinato** → USDA FoodData Central (macro+micro research-grade)
   - **Nessun match** → fallback stima Claude
3. Claude stima i **grammi** della porzione (le API danno valori per 100g); HARIA calcola totali
4. HARIA mostra stima, utente può correggere ("erano 120g di pasta")
5. Salva valori finali con fonte; cache in `food_db` per riuso

## Fonti dati nutrizionali

Solo gratuite, nessun costo ricorrente.

| Fonte | Uso | Note |
|-------|-----|------|
| **OpenFoodFacts** | barcode prodotti confezionati | 2.5M prodotti, ottima copertura italiana/EU, API gratis no limiti |
| **USDA FoodData Central** | alimenti grezzi/generici | 380k alimenti, micronutrienti affidabili, gratis no limiti, update trimestrale |
| **Claude (fallback)** | quando nessun match API | stima approssimata, sempre disponibile |

Strategia: prova API gratis prima (precise), Claude solo come rete di sicurezza. Risultati cache in `food_db` per ridurre chiamate.

## Schema dati (bozza)

```
meals(id, user_id, meal_type, eaten_at, kcal_total, protein_g, carbs_g, fat_g,
      fiber_g, sugar_g, sat_fat_g, sodium_mg, source, created_at)
meal_items(id, meal_id, name, grams, kcal, protein_g, carbs_g, fat_g)  # dettaglio per alimento
diet_profiles(user_id, age, sex, height_cm, weight_kg, goal, kcal_target, activity_level, allergies, preferences, restrictions)
weight_log(id, user_id, weight_kg, bmi, logged_at)   # storico peso + BMI calcolato
meal_plan(id, date, meal_type, planned_items, recipe, servings, created_at)
pantry(id, item, qty, unit, updated_at)        # opzionale, per spesa
water_log(id, user_id, ml, logged_at)          # fase 7 idratazione
food_db(id, name, barcode, kcal_per_100g, protein, carbs, fat, fiber, sugar, sat_fat, sodium, source, updated_at)  # cache OpenFoodFacts/USDA
```

## Tool Claude (bozza)

- `log_meal(user, meal_type, description)` — registra pasto, HARIA struttura + stima
- `get_meals(user?, date_range)` — query storico
- `set_diet_profile(user, ...)` — imposta/aggiorna profilo
- `plan_week(constraints?)` — genera piano settimanale
- `get_meal_plan(date_range)` — leggi piano
- `suggest_meal(meal_type, context?)` — cosa cucinare ora
- `generate_shopping_list(date_range)` — lista spesa dal piano

## Decisioni aperte

- ~~Stima nutrizionale: Claude vs API~~ → DECISO: OpenFoodFacts (barcode) + USDA (grezzi) + Claude fallback, cache in food_db
- Dispensa: tracciata attivamente o chiesta al momento della spesa
- Quanto delegare la struttura del pasto a Claude vs form fisso
