import json

from modules import economia


async def test_add_transazione_spesa_default_contanti(db, monkeypatch):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)

    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 20, "categoria": "alimentari", "descrizione": "frutta",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["conto"] == "contanti_andrea"
    assert data["importo"] == 20
    assert data["saldo_aggiornato"] == -20


async def test_add_transazione_entrata_conto_alias(db, monkeypatch):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)

    out = await economia.handle("add_transazione", {
        "tipo": "entrata", "importo": 1500, "categoria": "stipendio",
        "conto": "conto corrente", "data": "2026-06-01",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["conto"] == "bancoposta"
    assert data["saldo_aggiornato"] == 1500


async def test_add_transazione_conto_sconosciuto(db, monkeypatch):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)

    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "varie", "conto": "revolut",
    }, "u1")
    assert "non riconosciuto" in out


async def test_add_transazione_tipo_invalido(db):
    out = await economia.handle("add_transazione", {
        "tipo": "boh", "importo": 10, "categoria": "varie",
    }, "u1")
    assert "Tipo non valido" in out


async def test_add_transazione_importo_zero(db):
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 0, "categoria": "varie",
    }, "u1")
    assert "non può essere zero" in out


async def test_add_transazione_categoria_mancante(db):
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "  ",
    }, "u1")
    assert "Categoria mancante" in out


async def test_unknown_tool(db):
    out = await economia.handle("foo", {}, "u1")
    assert "Tool sconosciuto" in out


def _wire(monkeypatch, db):
    monkeypatch.setattr(economia, "add_transazione", db.add_transazione)
    monkeypatch.setattr(economia, "get_saldo", db.get_saldo)
    monkeypatch.setattr(economia, "get_saldi", db.get_saldi)
    monkeypatch.setattr(economia, "riepilogo_spese", db.riepilogo_spese)
    monkeypatch.setattr(economia, "normalize_categoria", db.normalize_categoria)
    monkeypatch.setattr(economia, "list_categorie", db.list_categorie)
    monkeypatch.setattr(economia, "rename_categoria", db.rename_categoria)
    monkeypatch.setattr(economia, "merge_categoria", db.merge_categoria)
    monkeypatch.setattr(economia, "reset_economia", db.reset_economia)
    monkeypatch.setattr(economia, "set_budget", db.set_budget)
    monkeypatch.setattr(economia, "delete_budget", db.delete_budget)
    monkeypatch.setattr(economia, "list_budget", db.list_budget)
    monkeypatch.setattr(economia, "get_budget_status", db.get_budget_status)
    monkeypatch.setattr(economia, "set_obiettivo", db.set_obiettivo)
    monkeypatch.setattr(economia, "accantona", db.accantona)
    monkeypatch.setattr(economia, "delete_obiettivo", db.delete_obiettivo)
    monkeypatch.setattr(economia, "get_obiettivi", db.get_obiettivi)
    monkeypatch.setattr(economia, "delete_categoria", db.delete_categoria)
    monkeypatch.setattr(economia, "list_conti", db.list_conti)
    monkeypatch.setattr(economia, "add_conto", db.add_conto)
    monkeypatch.setattr(economia, "update_conto", db.update_conto)
    monkeypatch.setattr(economia, "delete_conto", db.delete_conto)
    monkeypatch.setattr(economia, "list_transazioni", db.list_transazioni)
    monkeypatch.setattr(economia, "update_transazione", db.update_transazione)
    monkeypatch.setattr(economia, "delete_transazione", db.delete_transazione)


async def test_get_saldo_conto_singolo(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("postepay_andrea", "2026-06-01", -5, "spesa", "caffe")
    out = await economia.handle("get_saldo", {"conto": "postepay_andrea"}, "u1")
    data = json.loads(out)
    assert data["conto"] == "postepay_andrea"
    assert data["saldo"] == -5


async def test_get_saldo_tutti(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-01", -20, "spesa", "frutta")
    out = await economia.handle("get_saldo", {}, "u1")
    data = json.loads(out)
    assert "saldi" in data
    assert data["totale"] == -20


async def test_get_saldo_conto_sconosciuto(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("get_saldo", {"conto": "revolut"}, "u1")
    assert "non riconosciuto" in out


async def test_riepilogo_spese_tool(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-02", -200, "alimentari", "spesa")
    out = await economia.handle("riepilogo_spese", {
        "data_da": "2026-06-01", "data_a": "2026-06-30",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["uscite"] == -200
    assert data["conto"] == "tutti"
    assert data["periodo"]["da"] == "2026-06-01"


async def test_riepilogo_spese_tool_conto_sconosciuto(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("riepilogo_spese", {"conto": "revolut"}, "u1")
    assert "non riconosciuto" in out


async def test_add_transazione_normalizza_categoria(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "ALIMENTARI", "descrizione": "x",
    }, "u1")
    data = json.loads(out)
    assert data["categoria"] == "alimentari"


async def test_gestisci_categorie_lista(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {"azione": "lista"}, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert "alimentari" in data["categorie"]


async def test_gestisci_categorie_rinomina(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {
        "azione": "rinomina", "da": "svago", "a": "Tempo Libero",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["a"] == "tempo libero"


async def test_gestisci_categorie_rinomina_args_mancanti(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {"azione": "rinomina", "da": "svago"}, "u1")
    assert "servono" in out


async def test_gestisci_categorie_unisci(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.normalize_categoria("cibo")
    out = await economia.handle("gestisci_categorie", {
        "azione": "unisci", "da": "cibo", "a": "alimentari",
    }, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert "cibo" not in await db.list_categorie()


async def test_gestisci_categorie_azione_invalida(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {"azione": "boh"}, "u1")
    assert "Azione non valida" in out


async def test_reset_economia_anteprima_senza_confirm(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    out = await economia.handle("reset_economia", {}, "u1")
    data = json.loads(out)
    assert data["ok"] is False
    assert data["conferma_richiesta"] is True
    # nulla cancellato
    assert await db.get_saldo("contanti_andrea") == -10


async def test_reset_economia_confirm(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    out = await economia.handle("reset_economia", {"confirm": True}, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["transazioni_cancellate"] == 1
    assert await db.get_saldo("contanti_andrea") == 0


async def test_set_budget_tool(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("set_budget", {"categoria": "Alimentari", "importo": 300}, "u1")
    data = json.loads(out)
    assert data["azione"] == "impostato"
    assert data["categoria"] == "alimentari"
    assert data["budget"] == 300


async def test_set_budget_tool_rimuovi(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.set_budget("svago", 100)
    out = await economia.handle("set_budget", {"categoria": "svago", "importo": 0}, "u1")
    data = json.loads(out)
    assert data["azione"] == "rimosso"
    assert data["trovato"] is True


async def test_set_budget_tool_categoria_mancante(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("set_budget", {"categoria": " ", "importo": 100}, "u1")
    assert "Categoria mancante" in out


async def test_get_budget_status_tool_default_mese(db, monkeypatch):
    _wire(monkeypatch, db)
    from datetime import date
    oggi = date.today()
    await db.set_budget("alimentari", 300)
    await db.add_transazione("contanti_andrea", oggi.isoformat(), -50, "alimentari", "x")
    out = await economia.handle("get_budget_status", {}, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["mese"] == oggi.month
    assert data["budget"][0]["speso"] == 50


async def test_get_budget_status_tool_nessun_budget(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("get_budget_status", {}, "u1")
    data = json.loads(out)
    assert data.get("nessun_budget") is True


async def test_get_budget_status_tool_mese_invalido(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("get_budget_status", {"mese": 13}, "u1")
    assert "Mese non valido" in out


async def test_set_obiettivo_tool(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("set_obiettivo", {"nome": "vacanze", "target": 1000}, "u1")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["nome"] == "vacanze"
    assert data["target"] == 1000


async def test_set_obiettivo_tool_target_invalido(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("set_obiettivo", {"nome": "x", "target": 0}, "u1")
    assert "positivo" in out


async def test_set_obiettivo_tool_scadenza_invalida(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("set_obiettivo", {"nome": "x", "target": 100, "scadenza": "31-12-2026"}, "u1")
    assert "Scadenza non valida" in out


async def test_accantona_tool(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.set_obiettivo("vacanze", 1000)
    out = await economia.handle("accantona", {"nome": "vacanze", "importo": 200}, "u1")
    data = json.loads(out)
    assert data["accantonato"] == 200


async def test_accantona_tool_inesistente(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("accantona", {"nome": "boh", "importo": 50}, "u1")
    assert "non trovato" in out


async def test_get_obiettivi_tool_vuoto(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("get_obiettivi", {}, "u1")
    data = json.loads(out)
    assert data.get("nessun_obiettivo") is True


async def test_get_obiettivi_tool(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.set_obiettivo("vacanze", 1000)
    await db.accantona("vacanze", 250)
    out = await economia.handle("get_obiettivi", {}, "u1")
    data = json.loads(out)
    assert data["obiettivi"][0]["perc"] == 25.0


# ---- profilazione membri ----

async def test_membro_from_user_fallback(db, monkeypatch):
    # user_id non mappato -> primo membro (andrea)
    assert economia._membro_from_user("sconosciuto") == "andrea"


async def test_add_transazione_default_conto_per_membro(db, monkeypatch):
    _wire(monkeypatch, db)
    # speaker non mappato -> contanti_andrea
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 20, "categoria": "alimentari", "descrizione": "frutta",
    }, "u1")
    data = json.loads(out)
    assert data["conto"] == "contanti_andrea"


async def test_add_transazione_conto_esplicito_membro(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 30, "categoria": "carburante",
        "conto": "postepay_marina",
    }, "u1")
    data = json.loads(out)
    assert data["conto"] == "postepay_marina"


async def test_riepilogo_filtra_intestatario(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-01", -20, "alimentari", "x")
    await db.add_transazione("contanti_marina", "2026-06-01", -50, "alimentari", "y")
    out = await economia.handle("riepilogo_spese", {"intestatario": "marina"}, "u1")
    data = json.loads(out)
    assert data["uscite"] == -50
    assert data["intestatario"] == "marina"


async def test_riepilogo_intestatario_invalido(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("riepilogo_spese", {"intestatario": "pluto"}, "u1")
    assert "non valido" in out


async def test_get_saldo_per_intestatario(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-01", -20, "alimentari", "x")
    await db.add_transazione("contanti_marina", "2026-06-01", -50, "alimentari", "y")
    out = await economia.handle("get_saldo", {}, "u1")
    data = json.loads(out)
    assert data["per_intestatario"]["andrea"] == -20
    assert data["per_intestatario"]["marina"] == -50


async def test_gestisci_conti_lista_crea_disattiva(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_conti", {"azione": "lista"}, "u1")
    assert json.loads(out)["ok"] is True

    out = await economia.handle("gestisci_conti", {"azione": "crea", "nome": "revolut_marina", "tipo": "carta", "intestatario": "marina", "saldo_iniziale": 100}, "u1")
    assert json.loads(out)["azione"] == "crea"
    assert (await db.get_conto("revolut_marina"))["saldo_iniziale"] == 100

    out = await economia.handle("gestisci_conti", {"azione": "disattiva", "nome": "revolut_marina"}, "u1")
    assert json.loads(out)["ok"] is True
    assert "revolut_marina" not in [c["nome"] for c in await db.list_conti(solo_attivi=True)]


async def test_gestisci_conti_elimina_in_uso(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    out = await economia.handle("gestisci_conti", {"azione": "elimina", "nome": "contanti_andrea"}, "u1")
    assert "non lo elimino" in out


async def test_gestisci_transazioni_modifica_elimina(db, monkeypatch):
    _wire(monkeypatch, db)
    tid = await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    out = await economia.handle("gestisci_transazioni", {"azione": "modifica", "id": tid, "importo": -30}, "u1")
    assert json.loads(out)["ok"] is True
    assert (await db.list_transazioni())[0]["importo"] == -30

    out = await economia.handle("gestisci_transazioni", {"azione": "elimina", "id": tid}, "u1")
    assert json.loads(out)["ok"] is True
    assert await db.list_transazioni() == []


async def test_gestisci_transazioni_lista(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    out = await economia.handle("gestisci_transazioni", {"azione": "lista"}, "u1")
    assert len(json.loads(out)["transazioni"]) == 1


async def test_gestisci_categorie_crea_elimina(db, monkeypatch):
    _wire(monkeypatch, db)
    out = await economia.handle("gestisci_categorie", {"azione": "crea", "nome": "Viaggi"}, "u1")
    assert json.loads(out)["categoria"] == "viaggi"
    out = await economia.handle("gestisci_categorie", {"azione": "elimina", "nome": "viaggi"}, "u1")
    assert json.loads(out)["ok"] is True


async def test_gestisci_obiettivi_elimina(db, monkeypatch):
    _wire(monkeypatch, db)
    await db.set_obiettivo("vacanze", 1000)
    out = await economia.handle("gestisci_obiettivi", {"azione": "elimina", "nome": "vacanze"}, "u1")
    assert json.loads(out)["ok"] is True
    assert await db.get_obiettivi() == []


async def test_add_transazione_conto_custom(db, monkeypatch):
    # TASK 1: conto custom creato a runtime deve essere usabile
    _wire(monkeypatch, db)
    await db.add_conto("revolut_test", "carta")
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "svago", "conto": "revolut_test",
    }, "123")
    data = json.loads(out)
    assert data["ok"] is True
    assert data["conto"] == "revolut_test"


async def test_add_transazione_data_invalida(db, monkeypatch):
    # TASK 5: data non ISO -> errore, niente inserito
    _wire(monkeypatch, db)
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 10, "categoria": "svago", "data": "12/06/2026",
    }, "u1")
    assert "Data non valida" in out
    assert await db.list_transazioni() == []


async def test_add_transazione_applica_regola(db, monkeypatch):
    # la regola vale anche per l'inserimento manuale via chat
    _wire(monkeypatch, db)
    monkeypatch.setattr(economia, "list_regole", db.list_regole)
    await db.add_regola("baiano", "alimentari")
    out = await economia.handle("add_transazione", {
        "tipo": "spesa", "importo": 30, "categoria": "spesa", "descrizione": "baiano group",
    }, "u1")
    data = json.loads(out)
    assert data["categoria"] == "alimentari"


async def test_reset_anteprima_menziona_obiettivi(db, monkeypatch):
    # TASK 3: anteprima reset elenca i salvadanai
    _wire(monkeypatch, db)
    await db.set_obiettivo("vacanze", 1000)
    out = await economia.handle("reset_economia", {}, "u1")
    data = json.loads(out)
    assert "salvadanai" in data["msg"] or "obiettivi" in data["msg"]
    assert "obiettivi_attuali" in data


async def test_import_estratto_bytes_postepay(db, monkeypatch):
    _wire(monkeypatch, db)
    import econ_import
    # costruisci un csv postepay minimale
    csv = ("Data Contabile;Data Valuta;Importo (euro);Descrizione operazioni\n"
           "10/06/2026;10/06/2026;-12,50;GLOVO\n"
           "11/06/2026;11/06/2026;-9,99;AMAZON\n")
    out = await economia.import_estratto_bytes(csv.encode("utf-8"), "estratto.csv",
                                               "marina", "u1")
    assert "Postepay Marina" in out or "PostePay Marina" in out
    assert "2 movimenti importati" in out
    saldo = await db.get_saldo("postepay_marina")
    assert saldo == round(-12.50 - 9.99, 2)


async def test_dynamic_prompt_include_categorie(db):
    # db fixture ha gia' inizializzato memory.DB_PATH al test db con le categorie seed
    out = economia.dynamic_prompt()
    assert "alimentari" in out
    assert "ECONOMIA categorie" in out
