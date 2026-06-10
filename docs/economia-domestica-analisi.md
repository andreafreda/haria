# Gestione economica domestica — analisi know-how (per HARIA)

> Documento di analisi/ricognizione. Decisioni di indirizzo prese in §9 — il piano
> implementativo dettagliato è il prossimo step.

## 1. Problema da risolvere

Difficoltà a fine mese nel quadrare i conti: mancano visibilità su spese fisse vs
variabili, budget per categoria, e un collegamento (anche solo manuale/import) con
i metodi di pagamento usati: **PostePay, BancoPosta, PayPal**.

## 2. Metodologie di budgeting (cosa usano le app)

| Metodo | Logica | Pro | Contro |
|---|---|---|---|
| **50/30/20** | 50% bisogni, 30% desideri, 20% risparmio/debiti | Semplice, leggibile a colpo d'occhio | Percentuali teoriche, poco aderenti a spese reali (mutuo/affitto spesso >50%) |
| **Metodo 3F** (Fisso/Flessibile/Futuro) | Parte dai costi reali dell'utente, non da percentuali fisse; il "flessibile" si divide in 3-5 categorie (spesa, fuori pasto, trasporti, svago, varie) | Più realistico per famiglie italiane | Richiede censimento iniziale delle spese fisse |
| **Zero-based budgeting** | Entrate − uscite assegnate = 0 a fine mese, ogni euro ha una destinazione | Massimo controllo, individua sprechi | Richiede manutenzione costante (mensile) |
| **Envelope / cash stuffing** | Buste (fisiche o virtuali) per categoria, quando finisce il budget della busta si stop | Ottimo per categorie a rischio overspend (svago, fuori pasto) | Meno adatto a spese fisse/bollette |

**Osservazione**: queste metodologie non si escludono. Una buona app spesso
combina: spese fisse a parte (elenco con scadenze) + budget mensile a buste/categorie
per il variabile, con percentuali come *guida* non vincolo rigido.

## 3. App esistenti — riferimento competitivo

### Consumer (mobile, IT)
- **Spendee** — wallet condivisi, categorie, budget mensili, collegamento bancario opzionale. Buon riferimento UX per famiglie.
- **Monefy** — estrema semplicità, categorizzazione rapida.
- **Money Manager** — tracking affidabile, ampia diffusione.
- **GoodBudget** — envelope budgeting + sync multi-utente/dispositivo.
- **Plan & Multiply** — Metodo 3F, "buste digitali", gratuita.
- **Wallet / Expense Manager** — notifiche su spese ricorrenti/scadenze (bollette, bollo auto).

### Self-hosted open source (riferimento architetturale)
- **Firefly III** — sistema di contabilità a partita doppia, multi-account, multi-valuta,
  motore di regole (trigger → azioni) per auto-categorizzazione, budget, gestione
  bollette/abbonamenti, reportistica storica profonda. Web server + DB (Docker).
  → Modello dati di riferimento se vogliamo robustezza "contabile".
- **Actual Budget** — budgeting envelope-style (stile YNAB), zero-sum mensile,
  rollover degli importi non spesi, UI moderna, offline-first con sync.
  → Modello di riferimento se vogliamo UX "budget mensile" più che contabilità.

**Per HARIA**: non serve adottare uno di questi, ma il loro *modello dati* (conti,
transazioni, categorie, regole di categorizzazione, budget per categoria/periodo,
bollette ricorrenti con scadenza) è un buon punto di partenza concettuale, dato che
HARIA ha già pattern simile per `bollette` (DB SQLite + sensori MQTT + dashboard HA).

### Salvadanai / obiettivi di risparmio ("savings goals")

Componente presente in quasi tutte le app consumer, finora non coperto nell'analisi:

- **GoodBudget "Spaces"** — salvadanai virtuali dentro lo stesso budget envelope:
  accantoni per uno scopo specifico (es. "vacanza", "nuovo divano"), monitori
  progresso verso obiettivo.
- **Piggy Goals / Loot / HYPE / Revolut / Tinaba** — pattern comune: importo target +
  scadenza target → l'app calcola quota periodica (giornaliera/settimanale/mensile)
  necessaria, e traccia progresso con notifiche.
- **MoneyStats / Money Manager** — obiettivi finanziari nominali ("risparmia 200€/mese
  per comprare auto entro 18 mesi"), integrati con budget e categorie.

**Modello dato semplice riusabile**: tabella `obiettivi_risparmio` (nome, importo
target, importo accantonato, data target opzionale, categoria/icona) + funzione che
calcola quota mensile suggerita = (target − accantonato) / mesi rimanenti. Si integra
naturalmente con la dashboard stile bollette (sensore MQTT con % progresso).

## 4. Integrazioni con sistemi di pagamento

### PostePay / BancoPosta (PSD2 / Open Banking)
- Poste Italiane espone API PSD2 tramite la piattaforma cooperativa **CBI Globe**
  (conti BancoPosta e PostePay accessibili a Third Party Provider autorizzati).
- Accesso "diretto" richiede registrazione come **TPP autorizzato (licenza AISP +
  certificato eIDAS)** — confermato da pagina ufficiale Poste: serve "certificato per
  operare". **Non percorribile per un progetto personale/hobbistico**, nessuna via
  legale gratuita per un singolo individuo che vuole solo leggere i propri conti.
- Aggregatori terzi (es. **GoCardless Bank Account Data**, ex Nordigen) offrivano
  un livello gratuito per uso personale, ma **da luglio 2025 non accettano più nuove
  registrazioni** (account esistenti continuano a funzionare). Strada chiusa per noi.
- **Conclusione definitiva**: niente sync automatica Poste in HARIA, né ora né a
  breve — è un blocco regolamentare, non tecnico. Le alternative restano:
  1. **Import manuale CSV** — confermato fattibile: BancoPosta (MyPoste web →
     "Elenco movimenti" → export Excel/CSV/PDF, filtro periodo, storico online 36
     mesi) e Postepay (app/web → movimenti → "Esporta/Condividi" → CSV/testo/email,
     storico dall'attivazione carta), entrambi gratuiti senza API. Utente scarica
     periodicamente, manda a HARIA, parsing → import transazioni (stesso pattern OCR
     bollette, ma su CSV invece di PDF). Via più realistica.
     - **Promemoria periodico**: HARIA può ricordare ogni N giorni di fare l'export
       (riuso scheduler, pattern food_diary/bollette).
     - **Idea futura (da valutare con cautela)**: CLI di scraping automatico dal
       sito Poste. Rischi noti da affrontare prima di investirci: ToS Poste vietano
       probabilmente accesso automatizzato (rischio blocco conto), login richiede
       2FA/SPID/OTP (non automatizzabile senza intervento umano), credenziali Poste
       da custodire in HARIA = superficie d'attacco extra, scraper fragile a ogni
       redesign del sito. Stessi motivi per cui Switcho ha rimosso il collegamento
       conti pur avendo licenza regolare. Studio di fattibilità rimandato.
  2. **Notifiche push** dell'app bancaria → inoltrate a HARIA (es. da Telegram/Android
     tramite automazioni esterne) → parsing testo → registrazione automatica.
  3. **OCR su PDF** estratto conto (come già fatto per le bollette A2A).
  4. App terze (Wallet/Spendee Premium) dichiarano supporto Open Banking PSD2 per banche
     italiane in **sola lettura**, ma sono servizi chiusi/a pagamento — utile solo come
     ispirazione, non integrabile in HARIA.

### PayPal
- API REST ufficiale (OAuth2) per estrarre transazioni (`/v1/reporting/transactions`)
  — accessibile creando un'app developer PayPal (gratis per account personali/business).
  Più fattibile di PSD2 bancario perché non richiede licenza TPP, solo client
  id/secret dell'utente.
- → **Candidato realistico per integrazione automatica** (polling periodico,
  es. ogni notte, simile a `mqtt_pub` refresh).

### Riepilogo fattibilità integrazioni
| Sorgente | Automatico oggi? | Sforzo | Note |
|---|---|---|---|
| PayPal | Sì (API REST personale) | Medio | OAuth2 client credentials, polling transazioni |
| BancoPosta/PostePay | No (no TPP gratuito) | Alto/non fattibile | Solo via aggregatore a pagamento o TPP license |
| Estratto conto PDF | Manuale (assistito) | Basso | Stesso pattern OCR già usato per bollette |
| Inserimento manuale via chat | Sì | Basso | Pattern già esistente in HARIA (tool-calling) |

## 5. Building blocks già presenti in HARIA (riusabili)

- **Modulo `bollette`**: DB SQLite (tabella dedicata) + pubblicazione sensori MQTT
  retained → dashboard HA. Pattern direttamente riapplicabile a "transazioni" e
  "budget".
- **Tool-calling da chat**: l'utente può mandare PDF/testo, Claude estrae dati
  strutturati e chiama un tool che scrive nel DB.
- **Dashboard HA (Lovelace storage mode)**: già gestita via MCP, supporta
  apexcharts/statistics-graph per grafici storici.
- **Scheduler**: già usato per refresh periodici (food_diary) — riusabile per
  polling PayPal o promemoria scadenze.

## 6. Spunti di feature (grezzi, da discutere — nessuna priorità)

- Censimento spese fisse (affitto/mutuo, utenze, abbonamenti) con scadenza e importo,
  alert prima della scadenza.
- Budget mensile per categoria (variabile), con soglia e notifica avvicinamento/superamento.
- Inserimento spesa via chat ("ho speso 30€ al supermercato") → categorizzazione
  automatica (Claude) → salvataggio.
- Import CSV estratto conto / PDF → parsing → revisione utente → conferma.
- Integrazione PayPal via API per import automatico.
- Dashboard riepilogo mensile: entrate vs uscite, fisso vs variabile, % per categoria,
  trend storico (riuso pattern bollette).
- Riepilogo/promemoria proattivo (es. inizio mese: "a che punto sei col budget?").
- Multi-utente (HARIA già supporta `multi_user`): spese condivise/separate per membro
  famiglia.
- **Salvadanai/obiettivi di risparmio**: oggetti "obiettivo" (nome, target, accantonato,
  scadenza) con quota mensile suggerita e progresso visibile in dashboard.

## 7. Esiste una soluzione "tutto in uno" che copre già tutto questo?

Sì, sul mercato esistono app che coprono **insieme** spese fisse/variabili, budget a
percentuale, salvadanai/obiettivi e (in parte) sync bancaria:

- **GoodBudget** — envelope budgeting + Spaces (salvadanai) + multi-dispositivo/condiviso.
  Niente sync bancaria automatica (inserimento manuale o import CSV).
- **Spendee / Wallet (Premium)** — budget per categoria, obiettivi di risparmio, wallet
  condivisi, e dichiarano sync Open Banking PSD2 con banche italiane (sola lettura).
- **MoneyStats / Money Manager** — budget, obiettivi nominali, condivisione dati con
  la famiglia, multi-conto.
- **Firefly III** (self-hosted) — copre conti/budget/bollette/regole, ma **non ha un
  vero modulo "obiettivi di risparmio"** nativo (si simula con conti dedicati).

**Conclusione**: nessuna soluzione gratuita/open-source copre **tutto** allo stesso
livello (in particolare salvadanai + sync bancaria italiana automatica gratuita non
esistono insieme in nessun prodotto libero). Le app commerciali (Spendee/Wallet
Premium) ci si avvicinano di più ma sono servizi chiusi a pagamento, non integrabili
in HARIA — utili solo come **riferimento UX/feature**, non come componente da
riusare. Per HARIA resta valido l'approccio "costruire su misura riusando il pattern
bollette", con sync bancaria reale rimandata (vedi tabella fattibilità sopra).

## 9. Decisioni prese (indirizzo per il piano)

1. **Partenza**: fisso + variabile insieme, fin dall'inizio (no fasi separate).
2. **Modello dati**: "conti leggeri", via di mezzo tra contabile puro e solo-budget:
   - **Conti**: PostePay, BancoPosta, PayPal, Contanti — registry estendibile (pattern
     `bollette_def.UTILITIES`).
   - **Transazioni**: conto, categoria, importo, data, descrizione/merchant (es.
     "Amazon", "frutta").
   - **Saldo conto** = saldo iniziale + somma transazioni (no doppia entrata vera,
     niente trasferimenti tra conti come prima fase).
   - "Ho speso 20€ frutta" → tool chat → transazione su conto default (es. Contanti),
     categoria scelta/dedotta.
3. **Categorie**: personalizzabili dall'utente via chat (no registry fisso come
   bollette — l'utente crea/usa categorie libere, Claude le normalizza/abbina alle
   esistenti per evitare duplicati tipo "spesa" vs "Spesa alimentare").
4. **Salvadanai/obiettivi**: inclusi (vedi §3.1) — tabella `obiettivi_risparmio` con
   quota mensile suggerita.
5. **Integrazioni pagamento**:
   - **PayPal**: integrazione automatica via API REST (OAuth2, polling periodico) —
     fattibile e da includere.
   - **PostePay/BancoPosta**: NESSUNA automazione possibile (blocco regolamentare
     TPP/AISP, vedi §4). Import semi-automatico (CSV/PDF estratto conto via OCR,
     pattern bollette) come unica via.
6. **Dashboard**: da decidere in fase di piano (nuova pagina vs estensione
   consumi/bollette) — non bloccante per il modello dati.

## 10. Formato file estrazione — campione reale (analizzato)

Estrazioni Excel ottenute da MyPoste, struttura confermata su campione reale:

### BancoPosta (conto corrente) — `ListaMovimenti.xlsx`
- **Righe header (0-10)**: metadata — periodo (`da`/`a`), conto, intestatario,
  saldo contabile/disponibile. Da skippare al parsing.
- **Header tabella riga 11**: `Data Contabile | Data Valuta | Addebiti (euro) |
  Accrediti (euro) | Descrizione operazioni`
- Importo **non firmato**, su due colonne separate (Addebiti = uscite, Accrediti =
  entrate) → normalizzare a importo unico con segno (Addebiti positivi → negativo,
  Accrediti → positivo).
- Campione: 708 righe, periodo **esattamente 1 anno** (10/06/2025–09/06/2026) —
  **conferma il limite ~1 anno per l'export BancoPosta** (sospetto utente corretto;
  più stretto dei 36 mesi di conservazione documenti).
- Descrizione include causale + data operazione + n. operazione + ultime cifre carta
  (utile per dedup e per inferenza categoria/merchant).

### Postepay (carta) — `ListaMovimenti (1).xlsx`
- **Righe header (0-1)**: vuote/minime.
- **Header tabella riga 2**: `Data Contabile | Data Valuta | Importo (euro) |
  Descrizione operazioni`
- Importo **già firmato** (negativo = uscita, positivo = entrata/accredito P2P).
- Campione: 1094 righe, periodo **~2 anni** (14/05/2024–10/06/2026) — nessun limite
  di 1 anno qui, copre da quando esiste lo storico disponibile.
- Descrizione varia per tipo operazione: `Pagamento E-Commerce <merchant> ...`,
  `Pagamento POS <merchant> ...`, `P2P A <persona>`, `Commissioni pagamento ...`,
  `PAGAMENTO GOOGLE PAY POS ESERCENTE ...`. Buon segnale per categorizzazione
  automatica (es. "PAYPAL *NETFLIX" → abbonamenti, "Amazon.it*" → shopping, "Glovo"
  → fuori pasto).

### Implicazioni per kata 1/9
- Schema `econ_transazioni` deve avere **importo firmato unico** (normalizzazione
  in fase di import per BancoPosta).
- Campo `descrizione_raw` da conservare integralmente (oltre a una `descrizione`
  pulita/breve) — utile sia per dedup sia per re-categorizzazione futura via Claude.
- Parser import (kata 9) deve riconoscere il formato (header a riga 11 vs riga 2,
  colonne addebiti/accrediti vs importo) — due profili di parsing per BancoPosta e
  Postepay.
- Promemoria export (kata 8) per BancoPosta va impostato **almeno ogni ~10-11 mesi**
  per non perdere storico (limite 1 anno), per Postepay meno critico.

## 11. Piano di sviluppo incrementale (kata)

Ogni kata è una unità deployabile e verificabile da sola (versione bump + deploy +
verifica), nello spirito del lavoro già fatto su `bollette`. Nessun kata dipende da
funzionalità non ancora costruite in kata successivi.

### Kata 1 — Schema dati base (conti + transazioni)
- [ ] `econ_def.py`: registry conti estendibile (PostePay, BancoPosta, PayPal,
      Contanti) — pattern `bollette_def.UTILITIES`
- [ ] Tabella `econ_conti` (nome, saldo_iniziale)
- [ ] Tabella `econ_transazioni` (id, conto, categoria, importo, data, descrizione)
- [ ] `memory.py`: `add_conto`, `list_conti`, `add_transazione`, `get_saldo_conto`,
      `list_transazioni`
- [ ] Seed automatico conti default al primo avvio
- **Verifica**: script/console di test su DB, nessuna UI ancora

### Kata 2 — Modulo HARIA + inserimento manuale via chat
- [ ] `modules/economia.py`: tool `add_transazione` (conto, categoria, importo,
      descrizione, data opz. — default oggi)
- [ ] Registrazione in `modules/__init__.py` ALL + `config.yaml` (modulo
      `economia: true` + schema)
- **Verifica**: "ho speso 20€ in frutta" via Telegram → riga in `econ_transazioni`,
      conto default Contanti se non specificato

### Kata 3 — Query/riepilogo via chat
- [ ] Tool `get_saldo` (per conto o totale tutti i conti)
- [ ] Tool `riepilogo_spese` (per categoria/periodo, default mese corrente)
- **Verifica**: "quanto ho su postepay?", "quanto ho speso a giugno in spesa
      alimentare?"

### Kata 4 — Categorie personalizzate + normalizzazione
- [ ] Tabella `econ_categorie` (nome canonico, alias)
- [ ] In `add_transazione`: se categoria nuova simile a esistente, Claude propone
      match invece di creare duplicato (usa `esistente`/conferma come pattern bollette)
- **Verifica**: "spesa" e "Spesa alimentare" non creano due categorie distinte

### Kata 5 — Budget per categoria
- [ ] Tabella `econ_budget` (categoria, importo_mensile)
- [ ] Tool `set_budget`, `get_budget_status` (speso/budget/residuo per categoria,
      mese corrente)
- **Verifica**: "imposta budget 300€ per spesa alimentare" + "quanto mi resta?"

### Kata 6 — Sensori MQTT + dashboard base
- [ ] `mqtt_pub.publish_economia()`: saldi conti + speso/budget per categoria
      (riuso pattern `publish_bollette`)
- [ ] Nuova pagina dashboard HA "Economia": saldi conti, barre budget vs speso
- **Verifica**: sensori `sensor.haria_economia_*` visibili in HA, dashboard popolata

### Kata 7 — Salvadanai / obiettivi di risparmio
- [ ] Tabella `econ_obiettivi` (nome, target, accantonato, scadenza opz.)
- [ ] Tool `add_obiettivo`, `versa_obiettivo`, calcolo quota mensile suggerita
      (target − accantonato) / mesi rimanenti
- [ ] Sensori MQTT progresso obiettivi (% completamento)
- **Verifica**: "voglio risparmiare 1000€ per le ferie entro dicembre" → quota
      mensile calcolata, progresso visibile in dashboard

### Kata 8 — Promemoria export Poste
- [ ] Job scheduler periodico (configurabile, es. ogni 30gg) → messaggio Telegram
      "esporta movimenti BancoPosta/Postepay e mandameli"
- **Verifica**: notifica arriva puntuale

### Kata 9 — Import CSV BancoPosta/Postepay
- [ ] Tool `import_movimenti`: utente manda CSV → parsing → riepilogo proposto
      (N transazioni, range date, totale) → conferma → bulk insert in
      `econ_transazioni` (conto dedotto dal tipo file)
- [ ] Dedup: evita doppio import di righe già presenti (hash data+importo+descrizione)
- **Verifica**: import reale di un export CSV di prova, nessun duplicato

### Kata 10 — Integrazione PayPal automatica
- [ ] Config: `paypal_client_id`/`paypal_client_secret` in `config.yaml`
- [ ] `paypal_client.py`: OAuth2 client-credentials + fetch
      `/v1/reporting/transactions`
- [ ] Job scheduler notturno → import automatico in `econ_transazioni` (conto
      PayPal), stesso dedup di kata 9
- **Verifica**: transazioni PayPal del giorno prima appaiono automaticamente

### Kata 11 — Riepiloghi proattivi
- [ ] Job mensile (inizio mese): report budget vs speso mese precedente, stato
      obiettivi, via Telegram
- **Verifica**: messaggio automatico ricevuto a inizio mese

### Kata 12 — Hardening e rifiniture
- [ ] Edge case: conto/categoria inesistenti, importi negativi, date future
- [ ] Validazioni input tool, messaggi di errore chiari
- [ ] Aggiornamento doc/README modulo

---

**Fonti consultate**:
- [Plan & Multiply — Gestione Spese Personali 2026](https://www.planandmultiply.com/it/blog/gestione-spese-personali-guida-completa)
- [Money.it — Le 10 migliori app per la gestione delle spese 2026](https://www.money.it/migliori-app-per-la-gestione-delle-spese-2026)
- [Mister Credit — Regola 50/30/20](https://www.mistercredit.it/guide/budget-e-risparmio/regola-50-30-20-come-gestire-il-tuo-budget-in-modo-efficace/)
- [Talos.tools — Firefly III vs Actual Budget](https://talos.tools/compare/firefly-iii-vs-actual-budget)
- [ezBookkeeping — Feature comparison](https://ezbookkeeping.mayswind.net/comparison/)
- [Ramsey Solutions — Zero-Based Budgeting](https://www.ramseysolutions.com/budgeting/how-to-make-a-zero-based-budget)
- [Poste Italiane — Open Banking PSD2](https://www.posteitaliane.it/it/open-banking.html)
- [Agenda Digitale — Open Banking e PSD2](https://www.agendadigitale.eu/cittadinanza-digitale/pagamenti-digitali/open-banking-e-psd2-come-cambiano-i-pagamenti-al-dettaglio/)
- [GoCardless Bank Account Data — Quickstart](https://developer.gocardless.com/bank-account-data/quick-start-guide/)
- [Firefly III docs — GoCardless import](https://docs.firefly-iii.org/how-to/data-importer/import/gocardless/)
- [PayPal Developer — REST APIs](https://developer.paypal.com/api/rest/)
- [QuiFinanza — App bilancio familiare](https://quifinanza.it/innovazione/le-migliori-app-per-gestire-il-bilancio-familiare/51525/)
