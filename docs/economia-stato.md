# Modulo `economia` — stato sviluppo (handoff sessione)

> Documento di continuità tra sessioni Claude Code. Aggiornato: **2026-06-10**, HARIA **v0.1.83**.
> Piano completo: [`economia-domestica-analisi.md`](economia-domestica-analisi.md) §11 (12 kata).

## Obiettivo
Gestione economica domestica via chat HARIA. Problema utente: "non arriviamo a fine mese,
non ci troviamo coi conti". Tracciare spese/entrate su **conti reali + cash**, budget,
salvadanai; poi import storico reale Poste/PayPal.

## Approccio sviluppo
- **Kata incrementali**: ogni kata indipendente, deployabile, verificato end-to-end.
- **Ciclo per kata**: implementa (memory.py + modules/economia.py + eventuale mqtt_pub.py)
  → test pytest → review (`cavecrew-reviewer`) → commit/push → deploy HA → self-test live.
- **Test**: `haria/app/tests/`, pytest+pytest-asyncio, fixture `db` isolata (tmp_path).
  92 test al momento. Run: `cd haria/app && python -m pytest -q`.
- **Modello**: Opus 4.8. Kata "pesanti" (9 import CSV, 10 PayPal OAuth) confermati Opus.

## Fatto (kata 1-7) ✅ — tutto live su HA v0.1.83

| Kata | Cosa | Tool / funzioni |
|---|---|---|
| 1 | Schema base | `econ_conti`, `econ_transazioni`, `econ_def.CONTI` (bancoposta/postepay/paypal/contanti), CRUD |
| 2 | Inserimento chat | `add_transazione` (default conto=contanti, importo firmato) |
| 3 | Query | `get_saldo`, `riepilogo_spese` |
| 4 | Categorie custom | `econ_categorie`, `normalize_categoria` (dedup case-insensitive), `gestisci_categorie` (lista/rinomina/unisci), `dynamic_prompt` espone categorie |
| 5 | Budget categoria | `econ_budget`, `set_budget`, `get_budget_status` |
| 6 | Dashboard MQTT | `mqtt_pub.publish_economia` (device "HARIA Economia"): saldi, spese mese, budget |
| 7 | Salvadanai | `econ_obiettivi`, `set_obiettivo`, `accantona`, `get_obiettivi` (quota mensile suggerita) |

Extra fatti fuori-kata:
- **`reset_economia`** (confirm gate) — azzera transazioni+budget+obiettivi, opz. categorie/saldi.
- **Self-test channel**: pilotare flusso chat reale via `ha_manage_addon` proxy POST
  `/api/chat` (stesso engine di Telegram). NB: timeout proxy 30s → msg con tool-loop
  pesante può scadere (la request però viene eseguita server-side; verificare via DB).

## Da fare (kata 8-12) ⏳

Ordine consigliato: **9 → 11 → 8 → 12 → 10** (valore prima; 10 per ultimo, dipende da credenziali utente).

| Kata | Cosa | Peso | Note |
|---|---|---|---|
| 9 | **Import CSV BancoPosta/Postepay** | pesante (Opus) | Parser dual-format (vedi §10 analisi: BancoPosta importo a 2 colonne Addebiti/Accrediti, Postepay importo firmato singolo). Dedup via hash riga. Sblocca storico reale. File campione: vedi sotto. |
| 11 | Riepiloghi mensili proattivi | medio | scheduler (APScheduler), summary mensile spese/budget/obiettivi su Telegram |
| 8 | Promemoria export Poste | leggero | scheduler ricorrente "vai a fare estrazione" (BancoPosta export copre ~1 anno → reminder ~ogni 10-11 mesi) |
| 12 | Hardening / edge case | medio | rifinitura, validazioni, edge date/importi |
| 10 | **Integrazione PayPal auto** | pesante (Opus) | OAuth2 client-credentials, `/v1/reporting/transactions`, polling notturno. **Richiede azione utente**: creare app developer PayPal → client_id/secret in config. |

## Dati reali (per kata 9)
- Estrazioni reali fornite dall'utente (in `C:\Users\...\Downloads\`, **PII — mai committare**):
  - `ListaMovimenti.xlsx` BancoPosta (708 righe, ~1 anno). Header tabella riga 11:
    `Data Contabile | Data Valuta | Addebiti (euro) | Accrediti (euro) | Descrizione operazioni`.
    Importo NON firmato (2 colonne) → normalizzare a importo firmato unico.
  - `ListaMovimenti (1).xlsx` Postepay (1094 righe, ~2 anni). Header riga 2:
    `Data Contabile | Data Valuta | Importo (euro) | Descrizione operazioni`. Importo già firmato.
- **Analisi chiave**: prelievi contanti BancoPosta ~-11.700€/anno (buco nero non tracciato,
  probabile causa "non arriviamo a fine mese"); delivery cibo Postepay ~104€/mese.
- **Gap noto**: "RICARICA POSTEPAY" su BancoPosta = trasferimento interno BancoPosta→Postepay,
  NON spesa → serve gestione "trasferimento" per non doppio-conteggiare (valutare in kata 9).
- **Saldi reali** (baseline se serve): BancoPosta saldo disponibile +58,20€; Postepay dal campione.
  Attualmente i conti partono da `saldo_iniziale=0` (nessun import fatto).

## Deploy (procedura, da memoria progetto)
1. bump `haria/config.yaml` `version:`
2. commit + push
3. `ha_call_service('shell_command','haria_supervisor_repair')` (store pull)
4. `ha_call_service('homeassistant','reload_config_entry', entity_id='update.haria_update')`
   (fix coordinator stale, altrimenti install dà 500)
5. `ha_call_service('update','install', entity_id='update.haria_update')` (timeout = normale, build in bg)
6. verifica `ha_get_addon('ab6b3a45_haria')` → version aggiornata
7. NB modulo nuovo: aggiungere a `/config/haria_options.json` via FTP (porta 22, gotcha config cache)
   — già fatto per `economia`, non serve più.

## MQTT
- Broker Mosquitto (`core_mosquitto`) installato. Se `mqtt_pub` logga "Servizio MQTT non
  disponibile (status 400)": **restart addon Mosquitto** (ri-registra il servizio al
  Supervisor), poi restart HARIA → "MQTT connesso". Risolto in questa sessione.
- 8 sensori economia live sotto device "HARIA Economia".

## Possibile extra (non in piano)
- Card Lovelace dedicata economia (vista dashboard) — offerta, non fatta.
- Import scontrino da foto (OCR vision → add_transazione) — discusso, kata futuro.
