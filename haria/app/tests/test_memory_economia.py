import pytest

import econ_def


async def test_seed_conti_default(db):
    conti = await db.list_conti()
    nomi = {c["nome"] for c in conti}
    assert nomi == set(econ_def.CONTI.keys())
    for c in conti:
        assert c["tipo"] == econ_def.CONTI[c["nome"]]["tipo"]
        assert c["saldo_iniziale"] == 0
        assert c["attivo"] is True


async def test_get_conto_unknown(db):
    assert await db.get_conto("inesistente") is None


async def test_add_conto_custom(db):
    cid = await db.add_conto("revolut", "carta", saldo_iniziale=50)
    assert cid > 0
    c = await db.get_conto("revolut")
    assert c["tipo"] == "carta"
    assert c["saldo_iniziale"] == 50


async def test_add_conto_idempotente(db):
    id1 = await db.add_conto("revolut", "carta")
    id2 = await db.add_conto("revolut", "carta")
    assert id1 == id2


async def test_saldo_iniziale_no_transazioni(db):
    assert await db.get_saldo("contanti_andrea") == 0


async def test_add_transazione_e_saldo(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -20, "spesa", "frutta")
    await db.add_transazione("contanti_andrea", "2026-06-02", 100, "entrata", "stipendio")
    assert await db.get_saldo("contanti_andrea") == 80


async def test_add_transazione_conto_sconosciuto(db):
    with pytest.raises(ValueError):
        await db.add_transazione("non_esiste", "2026-06-01", -10, "spesa", "x")


async def test_get_saldo_conto_sconosciuto(db):
    with pytest.raises(ValueError):
        await db.get_saldo("non_esiste")


async def test_list_transazioni_filtri(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -20, "spesa", "frutta")
    await db.add_transazione("postepay_andrea", "2026-06-02", -5, "spesa", "caffe")
    await db.add_transazione("contanti_andrea", "2026-06-03", -10, "regali", "fiori")

    tutte = await db.list_transazioni()
    assert len(tutte) == 3

    solo_contanti = await db.list_transazioni(conto="contanti_andrea")
    assert len(solo_contanti) == 2
    assert all(t["conto"] == "contanti_andrea" for t in solo_contanti)

    solo_spesa = await db.list_transazioni(categoria="spesa")
    assert len(solo_spesa) == 2

    range_date = await db.list_transazioni(data_da="2026-06-02", data_a="2026-06-02")
    assert len(range_date) == 1
    assert range_date[0]["descrizione"] == "caffe"


async def test_list_transazioni_ordine_desc(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -1, "spesa", "a")
    await db.add_transazione("contanti_andrea", "2026-06-03", -1, "spesa", "b")
    await db.add_transazione("contanti_andrea", "2026-06-02", -1, "spesa", "c")

    righe = await db.list_transazioni(conto="contanti_andrea")
    assert [r["descrizione"] for r in righe] == ["b", "c", "a"]


async def test_delete_transazione(db):
    tid = await db.add_transazione("contanti_andrea", "2026-06-01", -20, "spesa", "frutta")
    assert await db.get_saldo("contanti_andrea") == -20

    ok = await db.delete_transazione(tid)
    assert ok is True
    assert await db.get_saldo("contanti_andrea") == 0

    assert await db.delete_transazione(tid) is False


async def test_get_saldi_tutti(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -20, "spesa", "frutta")
    await db.add_transazione("postepay_andrea", "2026-06-01", -5, "spesa", "caffe")
    saldi = {s["conto"]: s["saldo"] for s in await db.get_saldi()}
    assert saldi["contanti_andrea"] == -20
    assert saldi["postepay_andrea"] == -5
    assert saldi["bancoposta"] == 0
    assert set(saldi.keys()) == set(econ_def.CONTI.keys())


async def test_riepilogo_spese_totali_e_categorie(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", 1500, "stipendio", "")
    await db.add_transazione("contanti_andrea", "2026-06-02", -200, "alimentari", "spesa")
    await db.add_transazione("postepay_andrea", "2026-06-03", -50, "alimentari", "frutta")
    await db.add_transazione("postepay_andrea", "2026-06-04", -80, "trasporti", "benzina")

    rep = await db.riepilogo_spese()
    assert rep["entrate"] == 1500
    assert rep["uscite"] == -330
    assert rep["netto"] == 1170
    # ordinate per importo crescente (piu' negativo = piu' speso prima)
    assert rep["per_categoria"][0] == {"categoria": "alimentari", "totale": -250}
    assert {c["categoria"] for c in rep["per_categoria"]} == {"alimentari", "trasporti"}


async def test_riepilogo_spese_filtro_periodo_e_conto(db):
    await db.add_transazione("contanti_andrea", "2026-05-31", -10, "alimentari", "x")
    await db.add_transazione("contanti_andrea", "2026-06-15", -20, "alimentari", "y")
    await db.add_transazione("postepay_andrea", "2026-06-15", -99, "svago", "z")

    rep = await db.riepilogo_spese(data_da="2026-06-01", data_a="2026-06-30", conto="contanti_andrea")
    assert rep["uscite"] == -20
    assert len(rep["per_categoria"]) == 1
    assert rep["per_categoria"][0]["categoria"] == "alimentari"


async def test_riepilogo_spese_solo_entrate(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", 1500, "stipendio", "")
    rep = await db.riepilogo_spese()
    assert rep["entrate"] == 1500.0
    assert rep["uscite"] == 0.0
    assert isinstance(rep["uscite"], float)
    assert rep["netto"] == 1500.0
    assert rep["per_categoria"] == []


async def test_riepilogo_spese_vuoto(db):
    rep = await db.riepilogo_spese()
    assert rep["entrate"] == 0
    assert rep["uscite"] == 0
    assert rep["netto"] == 0
    assert rep["per_categoria"] == []


async def test_amount_rounding(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -19.995, "spesa", "x")
    await db.add_transazione("contanti_andrea", "2026-06-02", 0.001, "entrata", "y")
    assert await db.get_saldo("contanti_andrea") == -19.99


# ---- categorie ----

async def test_categorie_seed_default(db):
    cats = await db.list_categorie()
    assert set(econ_def.CATEGORIE_DEFAULT) <= set(cats)


async def test_normalize_categoria_match_case_insensitive(db):
    # 'alimentari' e' seedata
    assert await db.normalize_categoria("Alimentari") == "alimentari"
    assert await db.normalize_categoria("  ALIMENTARI ") == "alimentari"
    # nessuna nuova categoria creata
    cats = await db.list_categorie()
    assert cats.count("alimentari") == 1


async def test_normalize_categoria_nuova(db):
    prima = set(await db.list_categorie())
    out = await db.normalize_categoria("Criptovalute")
    assert out == "criptovalute"
    dopo = set(await db.list_categorie())
    assert dopo - prima == {"criptovalute"}


async def test_normalize_categoria_vuota(db):
    assert await db.normalize_categoria("   ") == "varie"
    assert await db.normalize_categoria(None) == "varie"


async def test_rename_categoria_propaga(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    ok = await db.rename_categoria("alimentari", "Cibo")
    assert ok is True
    cats = await db.list_categorie()
    assert "cibo" in cats
    assert "alimentari" not in cats
    righe = await db.list_transazioni(categoria="cibo")
    assert len(righe) == 1


async def test_rename_categoria_inesistente(db):
    assert await db.rename_categoria("nonesiste", "x") is False


async def test_rename_su_esistente_equivale_merge(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "carburante", "x")
    await db.add_transazione("contanti_andrea", "2026-06-02", -5, "trasporti", "y")
    ok = await db.rename_categoria("carburante", "trasporti")
    assert ok is True
    cats = await db.list_categorie()
    assert "carburante" not in cats
    assert "trasporti" in cats
    righe = await db.list_transazioni(categoria="trasporti")
    assert len(righe) == 2


async def test_merge_categoria(db):
    await db.normalize_categoria("cibo")
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "cibo", "x")
    await db.add_transazione("contanti_andrea", "2026-06-02", -5, "alimentari", "y")
    ok = await db.merge_categoria("cibo", "alimentari")
    assert ok is True
    cats = await db.list_categorie()
    assert "cibo" not in cats
    assert (await db.list_transazioni(categoria="alimentari")).__len__() == 2


async def test_merge_categoria_src_inesistente(db):
    assert await db.merge_categoria("nonesiste", "alimentari") is False


async def test_merge_categoria_dst_creata(db):
    await db.normalize_categoria("vecchia")
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "vecchia", "x")
    ok = await db.merge_categoria("vecchia", "nuovissima")
    assert ok is True
    cats = await db.list_categorie()
    assert "nuovissima" in cats
    assert "vecchia" not in cats


async def test_merge_categoria_su_se_stessa(db):
    assert await db.merge_categoria("alimentari", "alimentari") is False


# ---- reset ----

async def test_reset_economia_solo_transazioni(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    await db.add_transazione("postepay_andrea", "2026-06-02", -5, "svago", "y")
    res = await db.reset_economia()
    assert res["transazioni_cancellate"] == 2
    assert res["categorie_resettate"] == 0
    assert res["saldi_azzerati"] == 0
    assert await db.get_saldo("contanti_andrea") == 0
    # categorie restano
    assert "alimentari" in await db.list_categorie()


async def test_reset_economia_categorie(db):
    await db.normalize_categoria("categoria_custom")
    assert "categoria_custom" in await db.list_categorie()
    res = await db.reset_economia(reset_categorie=True)
    assert res["categorie_resettate"] > 0
    cats = await db.list_categorie()
    assert "categoria_custom" not in cats
    assert set(econ_def.CATEGORIE_DEFAULT) == set(cats)


async def test_reset_economia_saldi(db):
    await db.add_conto("revolut", "carta", saldo_iniziale=100)
    res = await db.reset_economia(reset_saldi=True)
    assert res["saldi_azzerati"] == 1
    assert (await db.get_conto("revolut"))["saldo_iniziale"] == 0


async def test_reset_economia_vuoto(db):
    res = await db.reset_economia()
    assert res["transazioni_cancellate"] == 0


async def test_reset_economia_cancella_budget(db):
    await db.set_budget("alimentari", 300)
    res = await db.reset_economia()
    assert res["budget_cancellati"] == 1
    assert await db.list_budget() == []


# ---- budget ----

async def test_set_budget_e_list(db):
    cat = await db.set_budget("Alimentari", 300)
    assert cat == "alimentari"
    b = await db.list_budget()
    assert b == [{"categoria": "alimentari", "importo": 300}]


async def test_set_budget_upsert(db):
    await db.set_budget("alimentari", 300)
    await db.set_budget("alimentari", 250)
    b = await db.list_budget()
    assert len(b) == 1
    assert b[0]["importo"] == 250


async def test_set_budget_importo_assoluto(db):
    await db.set_budget("svago", -50)
    assert (await db.list_budget())[0]["importo"] == 50


async def test_delete_budget(db):
    await db.set_budget("svago", 100)
    assert await db.delete_budget("Svago") is True
    assert await db.list_budget() == []
    assert await db.delete_budget("svago") is False


async def test_get_budget_status_speso_e_residuo(db):
    await db.set_budget("alimentari", 300)
    await db.add_transazione("contanti_andrea", "2026-06-05", -120, "alimentari", "spesa")
    await db.add_transazione("postepay_andrea", "2026-06-20", -80, "alimentari", "spesa2")
    # entrata e altra categoria non contano
    await db.add_transazione("contanti_andrea", "2026-06-10", 500, "stipendio", "")
    await db.add_transazione("contanti_andrea", "2026-06-10", -40, "svago", "cinema")

    st = await db.get_budget_status(2026, 6)
    assert len(st) == 1
    s = st[0]
    assert s["categoria"] == "alimentari"
    assert s["budget"] == 300
    assert s["speso"] == 200
    assert s["residuo"] == 100
    assert s["perc"] == 66.7
    assert s["sforato"] is False


async def test_get_budget_status_sforamento(db):
    await db.set_budget("ristoranti", 100)
    await db.add_transazione("postepay_andrea", "2026-06-05", -150, "ristoranti", "cena")
    st = await db.get_budget_status(2026, 6)
    assert st[0]["sforato"] is True
    assert st[0]["residuo"] == -50
    assert st[0]["perc"] == 150.0


async def test_get_budget_status_filtra_mese(db):
    await db.set_budget("alimentari", 300)
    await db.add_transazione("contanti_andrea", "2026-05-31", -100, "alimentari", "maggio")
    await db.add_transazione("contanti_andrea", "2026-06-01", -50, "alimentari", "giugno")
    st = await db.get_budget_status(2026, 6)
    assert st[0]["speso"] == 50


async def test_get_budget_status_nessun_budget(db):
    assert await db.get_budget_status(2026, 6) == []


async def test_get_budget_status_ordine_perc_desc(db):
    await db.set_budget("alimentari", 300)
    await db.set_budget("ristoranti", 100)
    await db.add_transazione("contanti_andrea", "2026-06-01", -60, "alimentari", "")   # 20%
    await db.add_transazione("contanti_andrea", "2026-06-01", -90, "ristoranti", "")   # 90%
    st = await db.get_budget_status(2026, 6)
    assert [s["categoria"] for s in st] == ["ristoranti", "alimentari"]


# ---- obiettivi / salvadanai ----

async def test_set_obiettivo_e_get(db):
    await db.set_obiettivo("vacanze", 1000)
    obs = await db.get_obiettivi()
    assert len(obs) == 1
    o = obs[0]
    assert o["nome"] == "vacanze"
    assert o["target"] == 1000
    assert o["accantonato"] == 0
    assert o["residuo"] == 1000
    assert o["perc"] == 0.0
    assert o["raggiunto"] is False
    assert o["quota_mensile"] is None  # nessuna scadenza


async def test_set_obiettivo_upsert_non_tocca_accantonato(db):
    await db.set_obiettivo("auto", 5000)
    await db.accantona("auto", 1000)
    await db.set_obiettivo("auto", 6000)  # aggiorna target
    o = (await db.get_obiettivi())[0]
    assert o["target"] == 6000
    assert o["accantonato"] == 1000


async def test_accantona_incrementa_e_sottrae(db):
    await db.set_obiettivo("vacanze", 1000)
    r = await db.accantona("Vacanze", 200)
    assert r["accantonato"] == 200
    r = await db.accantona("vacanze", 50)
    assert r["accantonato"] == 250
    # ritiro
    r = await db.accantona("vacanze", -100)
    assert r["accantonato"] == 150


async def test_accantona_non_sotto_zero(db):
    await db.set_obiettivo("vacanze", 1000)
    await db.accantona("vacanze", 50)
    r = await db.accantona("vacanze", -200)
    assert r["accantonato"] == 0


async def test_accantona_inesistente(db):
    assert await db.accantona("nonesiste", 100) is None


async def test_obiettivo_raggiunto(db):
    await db.set_obiettivo("regalo", 100)
    r = await db.accantona("regalo", 100)
    assert r["raggiunto"] is True
    o = (await db.get_obiettivi())[0]
    assert o["residuo"] == 0
    assert o["perc"] == 100.0
    assert o["quota_mensile"] == 0.0


async def test_quota_mensile_con_scadenza(db):
    from datetime import date
    # scadenza ~3 mesi avanti
    oggi = date.today()
    m = oggi.month + 3
    y = oggi.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    scad = date(y, m, oggi.day).isoformat()
    await db.set_obiettivo("vacanze", 1000, scad)
    await db.accantona("vacanze", 100)
    o = (await db.get_obiettivi())[0]
    assert o["mesi_rimanenti"] == 3
    assert o["quota_mensile"] == 300.0  # (1000-100)/3


async def test_quota_mensile_scaduto(db):
    await db.set_obiettivo("vacanze", 1000, "2020-01-01")
    await db.accantona("vacanze", 100)
    o = (await db.get_obiettivi())[0]
    assert o["mesi_rimanenti"] == 0
    assert o["quota_mensile"] == 900.0  # serve tutto subito


async def test_delete_obiettivo(db):
    await db.set_obiettivo("x", 100)
    assert await db.delete_obiettivo("X") is True
    assert await db.get_obiettivi() == []
    assert await db.delete_obiettivo("x") is False


async def test_reset_cancella_obiettivi(db):
    await db.set_obiettivo("x", 100)
    res = await db.reset_economia()
    assert res["obiettivi_cancellati"] == 1
    assert await db.get_obiettivi() == []


# ---- storico mensile / annuale ----

async def test_andamento_mensile(db):
    await db.add_transazione("contanti_andrea", "2026-01-15", -100, "alimentari", "")
    await db.add_transazione("contanti_andrea", "2026-01-20", 500, "stipendio", "")
    await db.add_transazione("contanti_andrea", "2026-03-10", -50, "svago", "")
    # anno diverso -> escluso
    await db.add_transazione("contanti_andrea", "2025-06-01", -999, "altro", "")

    mesi = await db.andamento_mensile(2026)
    assert len(mesi) == 12
    gen = mesi[0]
    assert gen["mese"] == 1
    assert gen["entrate"] == 500
    assert gen["uscite"] == 100
    assert gen["netto"] == 400
    assert mesi[2]["uscite"] == 50   # marzo
    assert mesi[1]["uscite"] == 0    # febbraio vuoto


async def test_andamento_mensile_filtro_intestatario(db):
    await db.add_transazione("contanti_andrea", "2026-01-15", -100, "alimentari", "")
    await db.add_transazione("contanti_marina", "2026-01-15", -40, "alimentari", "")
    mesi = await db.andamento_mensile(2026, intestatario="marina")
    assert mesi[0]["uscite"] == 40


async def test_spese_categoria_anno(db):
    await db.add_transazione("contanti_andrea", "2026-01-15", -100, "alimentari", "")
    await db.add_transazione("contanti_andrea", "2026-02-10", -80, "alimentari", "")
    await db.add_transazione("contanti_andrea", "2026-01-05", -200, "shopping", "")
    await db.add_transazione("contanti_andrea", "2026-01-05", 999, "stipendio", "")  # entrata esclusa

    spese = await db.spese_categoria_anno(2026)
    # ordinato per totale desc: shopping 200, alimentari 180
    assert spese[0]["categoria"] == "shopping"
    assert spese[0]["totale"] == 200
    assert spese[0]["mesi"][0] == 200   # gennaio
    ali = spese[1]
    assert ali["categoria"] == "alimentari"
    assert ali["totale"] == 180
    assert ali["mesi"][0] == 100   # gennaio
    assert ali["mesi"][1] == 80    # febbraio
    # stipendio (entrata) non presente
    assert all(s["categoria"] != "stipendio" for s in spese)


async def test_anni_con_dati(db):
    await db.add_transazione("contanti_andrea", "2024-05-01", -10, "altro", "")
    await db.add_transazione("contanti_andrea", "2026-01-01", -10, "altro", "")
    assert await db.anni_con_dati() == [2024, 2026]


async def test_anni_con_dati_vuoto(db):
    assert await db.anni_con_dati() == []


# ---- CRUD conti / transazioni / categorie ----

async def test_update_conto(db):
    await db.add_conto("revolut", "carta", 10, "andrea")
    assert await db.update_conto("revolut", saldo_iniziale=50, intestatario="marina") is True
    c = await db.get_conto("revolut")
    assert c["saldo_iniziale"] == 50
    assert c["intestatario"] == "marina"


async def test_update_conto_rinomina(db):
    await db.add_conto("revolut", "carta")
    assert await db.update_conto("revolut", nuovo_nome="revolut_andrea") is True
    assert await db.get_conto("revolut") is None
    assert await db.get_conto("revolut_andrea") is not None


async def test_update_conto_inesistente(db):
    assert await db.update_conto("boh", saldo_iniziale=1) is False


async def test_update_conto_disattiva(db):
    await db.add_conto("revolut", "carta")
    await db.update_conto("revolut", attivo=False)
    attivi = [c["nome"] for c in await db.list_conti(solo_attivi=True)]
    assert "revolut" not in attivi
    tutti = [c["nome"] for c in await db.list_conti(solo_attivi=False)]
    assert "revolut" in tutti


async def test_delete_conto_vuoto(db):
    await db.add_conto("revolut", "carta")
    res = await db.delete_conto("revolut")
    assert res["ok"] is True
    assert await db.get_conto("revolut") is None


async def test_delete_conto_in_uso(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    res = await db.delete_conto("contanti_andrea")
    assert res["ok"] is False
    assert res["in_uso"] is True
    assert res["transazioni"] == 1


async def test_delete_conto_inesistente(db):
    res = await db.delete_conto("boh")
    assert res["trovato"] is False


async def test_update_transazione(db):
    tid = await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    assert await db.update_transazione(tid, importo=-25, categoria="svago") is True
    righe = await db.list_transazioni()
    assert righe[0]["importo"] == -25
    assert righe[0]["categoria"] == "svago"


async def test_update_transazione_cambia_conto(db):
    tid = await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    assert await db.update_transazione(tid, conto="postepay_andrea") is True
    assert await db.get_saldo("contanti_andrea") == 0
    assert await db.get_saldo("postepay_andrea") == -10


async def test_update_transazione_conto_sconosciuto(db):
    tid = await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    assert await db.update_transazione(tid, conto="boh") is False


async def test_delete_categoria_non_usata(db):
    await db.normalize_categoria("temporanea")
    res = await db.delete_categoria("Temporanea")
    assert res["ok"] is True
    assert "temporanea" not in await db.list_categorie()


async def test_delete_categoria_in_uso(db):
    await db.add_transazione("contanti_andrea", "2026-06-01", -10, "alimentari", "x")
    res = await db.delete_categoria("alimentari")
    assert res["ok"] is False
    assert res["in_uso"] is True


async def test_delete_categoria_inesistente(db):
    res = await db.delete_categoria("boh")
    assert res["trovato"] is False


# ---- bugfix review 2026-06-11 ----

async def test_update_conto_rename_collisione(db):
    # TASK 17: rename su nome esistente -> False, niente crash
    await db.add_conto("a_conto", "carta")
    await db.add_conto("b_conto", "carta")
    assert await db.update_conto("a_conto", nuovo_nome="b_conto") is False
    assert await db.get_conto("a_conto") is not None  # invariato


async def test_set_bolletta_range_conserva_totale(db):
    # TASK 16: somma dei mesi == total (no perdita centesimi)
    await db.set_bolletta_range("corrente", "costo", 2026, 1, 3, 100.0)
    csv = await db.get_bolletta_csv("corrente", "costo", 2026)
    vals = [float(x) for x in csv.split(",")]
    assert round(sum(vals), 2) == 100.0


async def test_migrazione_conti_generici_una_tantum(db):
    # TASK 2: un conto 'paypal' creato dall'utente non viene ri-disattivato
    await db.add_conto("paypal", "wallet")
    assert (await db.get_conto("paypal"))["attivo"] is True
    await db.init_db()  # secondo avvio
    assert (await db.get_conto("paypal"))["attivo"] is True
