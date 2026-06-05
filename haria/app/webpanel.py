"""Pannello web HARIA (ingress HA, porta 8099).

Dashboard sola-lettura:
  - Piano pasti settimanale (cosa cucinare)
  - Diario per membro (cosa ha mangiato + kcal)
  - Profili + peso/BMI
  - Lista della spesa
"""
import logging
import html
from datetime import date, timedelta
from aiohttp import web
import csv
import io
from memory import (
    get_meal_plan, list_profiles, list_members_with_meals,
    get_meals, get_day_totals, get_hydration_day, get_shopping_list,
    get_weight_history, export_meals, get_profile,
    get_pantry, get_pantry_expiring, get_logged_days,
    toggle_shopping_item, upsert_profile,
    get_user_briefings, add_briefing, update_briefing, deactivate_briefing,
)
from modules.food_diary import compute_macro_targets
from modules import news
import scheduler
import config as cfg
from claude_engine import chat

logger = logging.getLogger(__name__)


def _e(v) -> str:
    """Escape HTML di un valore (anti-XSS su dati utente/AI). None/'' -> ''."""
    if v is None:
        return ""
    return html.escape(str(v))


def _chat_user() -> dict | None:
    """Primo utente configurato (target chat ingress/dashboard)."""
    users = cfg.get("users", [])
    return users[0] if users else None

_MEAL_ORDER = {"colazione": 0, "pranzo": 1, "snack": 2, "cena": 3}
_GIORNI = ["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"]

_CSS = """
body{font-family:system-ui,sans-serif;margin:0;background:#f4f6f8;color:#222}
header{background:#3367d6;color:#fff;padding:14px 20px;font-size:20px;font-weight:600}
nav{background:#fff;padding:8px 20px;border-bottom:1px solid #ddd}
nav a{margin-right:16px;color:#3367d6;text-decoration:none;font-weight:500}
main{padding:20px;max-width:1100px;margin:0 auto}
h2{margin-top:28px;color:#3367d6}
table{border-collapse:collapse;width:100%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1);margin-bottom:16px}
th,td{padding:8px 12px;border-bottom:1px solid #eee;text-align:left;font-size:14px}
th{background:#eef2fb}
.card{background:#fff;border-radius:8px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.1);margin-bottom:16px}
.kcal{font-weight:600;color:#3367d6}
.muted{color:#888;font-size:13px}
.chk{color:#39a845}
#log{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}
.msg{padding:8px 12px;border-radius:12px;max-width:80%;white-space:pre-wrap;font-size:14px}
.msg.u{align-self:flex-end;background:#3367d6;color:#fff}
.msg.a{align-self:flex-start;background:#fff;border:1px solid #ddd}
#cform{display:flex;gap:8px}
#cin{flex:1;padding:10px;border:1px solid #ccc;border-radius:8px;font-size:14px}
#cbtn{padding:10px 18px;background:#3367d6;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer}
#cbtn:disabled{opacity:.5}
input[type=checkbox]{width:18px;height:18px;cursor:pointer}
.efield{display:inline-block;margin:4px 8px 4px 0}
.efield label{font-size:12px;color:#888;display:block}
.efield input,.efield select{padding:6px;border:1px solid #ccc;border-radius:6px;font-size:14px;width:120px}
.btn{padding:8px 16px;background:#3367d6;color:#fff;border:none;border-radius:6px;font-weight:600;cursor:pointer}
.btn:disabled{opacity:.5}
.bar{display:inline-block;background:#3367d6;border-radius:3px 3px 0 0;width:18px;vertical-align:bottom}
.chart{display:flex;align-items:flex-end;gap:4px;height:120px;padding:8px 0}
.chart .col{display:flex;flex-direction:column;align-items:center;justify-content:flex-end;font-size:10px;color:#888}
"""


def _page(title: str, body: str) -> web.Response:
    page = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HARIA — {title}</title><style>{_CSS}</style></head><body>
<header>🤖 HARIA — Diario Alimentare</header>
<nav><a href="./">Piano</a><a href="./month">Mese</a><a href="./diary">Diario</a><a href="./profiles">Profili</a><a href="./shopping">Spesa</a><a href="./pantry">Dispensa</a><a href="./briefings">Notizie</a><a href="./chat">Chat</a><a href="./export.csv">Export CSV</a></nav>
<main>{body}</main></body></html>"""
    return web.Response(text=page, content_type="text/html")


def _day_card(label: str, meals: list[dict]) -> str:
    """Render una card-giorno del piano pasti."""
    out = f"<div class='card'><b>{label}</b>"
    if not meals:
        out += " <span class='muted'>— niente pianificato</span></div>"
        return out
    day_kcal = sum(m["kcal"] or 0 for m in meals if not (m.get("member") or "").strip())
    if day_kcal:
        out += f" <span class='kcal'>{round(day_kcal)} kcal</span>"
    out += "<table><tr><th>Pasto</th><th>Chi</th><th>Portate</th><th>Ricetta</th><th>Porz.</th><th>kcal</th></tr>"
    for m in meals:
        who = (m.get("member") or "").strip()
        who_lbl = _e(who.capitalize()) if who else "<span class='muted'>comune</span>"
        out += (f"<tr><td>{_e(m['meal_type'])}</td><td>{who_lbl}</td><td>{_e(m['items'] or '')}</td>"
                f"<td class='muted'>{_e(m['recipe'] or '')}</td><td>{_e(m['servings'] or '')}</td>"
                f"<td>{round(m['kcal']) if m['kcal'] else ''}</td></tr>")
    out += "</table></div>"
    return out


async def _h_plan(request):
    start = date.today() - timedelta(days=date.today().weekday())
    days = [(start + timedelta(days=i)) for i in range(7)]
    plan = await get_meal_plan(days[0].isoformat(), days[-1].isoformat())
    by_day = {}
    for m in plan:
        by_day.setdefault(m["date"], []).append(m)
    body = "<h2>Piano settimanale — cosa cucinare</h2>"
    if not plan:
        body += "<div class='card muted'>Nessun piano per questa settimana. Chiedi a HARIA su Telegram: «pianifica la settimana».</div>"
    for i, d in enumerate(days):
        meals = sorted(by_day.get(d.isoformat(), []),
                       key=lambda m: (_MEAL_ORDER.get(m["meal_type"], 9), m.get("member") or ""))
        body += _day_card(f"{_GIORNI[i]} {d.strftime('%d/%m')}", meals)
    return _page("Piano", body)


async def _h_month(request):
    today = date.today()
    first = today.replace(day=1)
    nxt = first.replace(year=first.year + 1, month=1) if first.month == 12 \
        else first.replace(month=first.month + 1)
    last = nxt - timedelta(days=1)
    days = [(first + timedelta(days=i)) for i in range((last - first).days + 1)]
    plan = await get_meal_plan(first.isoformat(), last.isoformat())
    by_day = {}
    for m in plan:
        by_day.setdefault(m["date"], []).append(m)
    mese_nome = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
                 "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"][first.month - 1]
    body = f"<h2>Piano mensile — {mese_nome} {first.year}</h2>"
    if not plan:
        body += "<div class='card muted'>Nessun piano per questo mese. Chiedi a HARIA: «pianifica la settimana».</div>"
    for d in days:
        meals = sorted(by_day.get(d.isoformat(), []),
                       key=lambda m: (_MEAL_ORDER.get(m["meal_type"], 9), m.get("member") or ""))
        if not meals:
            continue  # mese lungo: salta giorni vuoti
        body += _day_card(f"{_GIORNI[d.weekday()]} {d.strftime('%d/%m')}", meals)
    return _page("Mese", body)


async def _h_diary(request):
    day = request.query.get("date") or date.today().isoformat()
    try:
        d = date.fromisoformat(day)
    except ValueError:
        d = date.today()
        day = d.isoformat()
    prev = (d - timedelta(days=1)).isoformat()
    nxt = (d + timedelta(days=1)).isoformat()
    members = await list_members_with_meals(day)
    body = (f"<h2>Diario del {day}</h2>"
            f"<div class='muted' style='margin-bottom:10px'>"
            f"<a href='./diary?date={prev}'>‹ {prev}</a> · "
            f"<a href='./diary'>oggi</a> · "
            f"<a href='./diary?date={nxt}'>{nxt} ›</a></div>")
    if not members:
        body += "<div class='card muted'>Nessun pasto registrato in questa data.</div>"
    for member in members:
        meals = await get_meals(member, day + "T00:00:00", day + "T23:59:59")
        totals = await get_day_totals(member, day)
        hydr = await get_hydration_day(member, day)
        p = await get_profile(member)
        kcal_t = p.get("kcal_target") if p else None
        macros = compute_macro_targets(kcal_t, p.get("weight_kg")) if p else None
        body += (f"<div class='card'><b>{_e(member.capitalize())}</b> "
                 f"<span class='kcal'>{totals['kcal']} kcal</span> "
                 f"<span class='muted'>P {totals['protein_g']}g · C {totals['carbs_g']}g · G {totals['fat_g']}g · 💧 {hydr['ml_total']} ml</span>")
        if kcal_t or macros:
            def _vt(val, tgt, unit=""):
                if not tgt:
                    return f"{round(val)}{unit}"
                delta = round(val - tgt)
                seg = "+" if delta > 0 else ""
                return f"{round(val)}/{round(tgt)}{unit} <span class='muted'>({seg}{delta})</span>"
            body += ("<div class='muted' style='margin-top:6px'>vs target — "
                     f"kcal {_vt(totals['kcal'], kcal_t)} · "
                     f"P {_vt(totals['protein_g'], macros['protein_target_g'] if macros else None, 'g')} · "
                     f"C {_vt(totals['carbs_g'], macros['carbs_target_g'] if macros else None, 'g')} · "
                     f"G {_vt(totals['fat_g'], macros['fat_target_g'] if macros else None, 'g')}</div>")
        body += (f"<div class='muted' style='margin-top:6px'>micro — "
                 f"fibre {totals['fiber_g']}g · zuccheri {totals['sugar_g']}g · "
                 f"saturi {totals['sat_fat_g']}g · sodio {totals['sodium_mg']}mg</div>"
                 f"<div class='muted' style='margin-top:4px'>vit/min — "
                 f"vit C {totals['vit_c_mg']}mg · vit D {totals['vit_d_ug']}µg · "
                 f"ferro {totals['iron_mg']}mg · calcio {totals['calcium_mg']}mg · "
                 f"potassio {totals['potassium_mg']}mg · magnesio {totals['magnesium_mg']}mg</div>")
        if meals:
            body += "<table><tr><th>Pasto</th><th>Descrizione</th><th>kcal</th></tr>"
            for m in sorted(meals, key=lambda x: _MEAL_ORDER.get(x['meal_type'], 9)):
                body += f"<tr><td>{_e(m['meal_type'])}</td><td>{_e(m['description'])}</td><td>{m['kcal_total'] or ''}</td></tr>"
            body += "</table>"
        body += "</div>"
    logged = await get_logged_days(30)
    if logged:
        chrono = sorted(logged, key=lambda x: x["day"])
        kmax = max((c["kcal"] or 0) for c in chrono) or 1
        body += "<h3 style='margin-top:24px'>Andamento kcal/giorno</h3><div class='card'><div class='chart'>"
        for c in chrono:
            k = c["kcal"] or 0
            h = round(100 * k / kmax) + 2
            body += (f"<div class='col' title='{c['day']}: {k} kcal'>"
                     f"<span class='bar' style='height:{h}px'></span>"
                     f"{c['day'][5:]}</div>")
        body += "</div></div>"
        body += "<h3 style='margin-top:24px'>Storico (ultimi 30 giorni con pasti registrati)</h3>"
        body += "<table><tr><th>Giorno</th><th>Membri</th><th>Pasti</th><th>kcal</th></tr>"
        for ld in logged:
            body += (f"<tr><td><a href='./diary?date={ld['day']}'>{ld['day']}</a></td>"
                     f"<td>{ld['members']}</td><td>{ld['meals']}</td><td>{ld['kcal']}</td></tr>")
        body += "</table>"
    return _page("Diario", body)


async def _h_profiles(request):
    profs = await list_profiles()
    body = "<h2>Profili famiglia</h2>"
    if not profs:
        body += "<div class='card muted'>Nessun profilo. Chiedi a HARIA di impostarlo.</div>"
    else:
        body += ("<table><tr><th>Membro</th><th>Sesso</th><th>Età</th><th>Altezza</th>"
                 "<th>Peso</th><th>BMI</th><th>Obiettivo</th><th>Attività</th><th>kcal/g</th>"
                 "<th>Prot. target</th><th>Carbo target</th><th>Grassi target</th></tr>")
        for p in profs:
            macros = compute_macro_targets(p.get("kcal_target"), p.get("weight_kg"))
            pt = f"{macros['protein_target_g']}g" if macros else ""
            ct = f"{macros['carbs_target_g']}g" if macros else ""
            gt = f"{macros['fat_target_g']}g" if macros else ""
            body += (f"<tr><td>{_e(p['member'])}</td><td>{_e(p['sex'] or '')}</td><td>{_e(p['age'] or '')}</td>"
                     f"<td>{_e(p['height_cm'] or '')}</td><td>{_e(p['weight_kg'] or '')}</td><td>{_e(p['bmi'] or '')}</td>"
                     f"<td>{_e(p['goal'] or '')}</td><td>{_e(p['activity_level'] or '')}</td><td>{_e(p['kcal_target'] or '')}</td>"
                     f"<td>{pt}</td><td>{ct}</td><td>{gt}</td></tr>")
        body += "</table>"
        for p in profs:
            hist = await get_weight_history(p["member"], limit=10)
            if hist:
                body += f"<div class='card'><b>Peso {_e(p['member'])}</b><table><tr><th>Data</th><th>kg</th><th>BMI</th></tr>"
                for h in hist:
                    body += f"<tr><td>{_e(h['logged_at'])}</td><td>{_e(h['weight_kg'])}</td><td>{_e(h['bmi'] or '')}</td></tr>"
                body += "</table></div>"
        body += "<h2>Modifica profilo</h2>"
        for p in profs:
            body += _profile_edit_form(p)
        body += """<script>
document.querySelectorAll('.prof-edit').forEach(f=>f.addEventListener('submit',async e=>{
  e.preventDefault();const btn=f.querySelector('button'),msg=f.querySelector('.savemsg');
  const d={member:f.dataset.member};
  f.querySelectorAll('input,select').forEach(i=>{if(i.name)d[i.name]=i.value;});
  btn.disabled=true;msg.textContent='…';
  try{const r=await fetch('./api/profile/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(d)});const j=await r.json();
    msg.textContent=j.ok?('✔ salvato'+(j.bmi?' (BMI '+j.bmi+')'):''):(j.error||'errore');}
  catch(err){msg.textContent='errore di rete';}
  btn.disabled=false;}));
</script>"""
    return _page("Profili", body)


_GOALS = ["", "mantenimento", "dimagrimento", "aumento massa"]
_ACTS = ["", "sedentario", "leggero", "moderato", "intenso", "molto intenso"]
_SEXES = ["", "M", "F"]


def _profile_edit_form(p: dict) -> str:
    def num(name, label, val):
        return (f"<div class='efield'><label>{label}</label>"
                f"<input type='number' step='any' name='{name}' value='{_e(val if val is not None else '')}'></div>")

    def sel(name, label, val, opts):
        o = "".join(f"<option {'selected' if (val or '')==x else ''}>{_e(x)}</option>" for x in opts)
        return f"<div class='efield'><label>{label}</label><select name='{name}'>{o}</select></div>"

    def txt(name, label, val):
        return (f"<div class='efield'><label>{label}</label>"
                f"<input type='text' name='{name}' value='{_e(val or '')}'></div>")

    return (f"<form class='card prof-edit' data-member=\"{_e(p['member'])}\">"
            f"<b>{_e(p['member'].capitalize())}</b><br>"
            + sel("sex", "Sesso", p.get("sex"), _SEXES)
            + num("age", "Età", p.get("age"))
            + num("height_cm", "Altezza cm", p.get("height_cm"))
            + num("weight_kg", "Peso kg", p.get("weight_kg"))
            + sel("goal", "Obiettivo", p.get("goal"), _GOALS)
            + sel("activity_level", "Attività", p.get("activity_level"), _ACTS)
            + num("kcal_target", "kcal/g", p.get("kcal_target"))
            + txt("allergies", "Allergie", p.get("allergies"))
            + txt("preferences", "Preferenze", p.get("preferences"))
            + txt("restrictions", "Restrizioni", p.get("restrictions"))
            + "<br><button class='btn' type='submit'>Salva</button>"
            "<span class='muted savemsg' style='margin-left:10px'></span></form>")


async def _h_profile_save(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "JSON non valido"}, status=400)
    member = (data.get("member") or "").strip().lower()
    if not member:
        return web.json_response({"error": "Membro mancante"}, status=400)
    fields: dict = {}
    for k in ("sex", "goal", "activity_level", "allergies", "preferences", "restrictions"):
        v = (data.get(k) or "").strip()
        fields[k] = v or None
    for k in ("age", "height_cm", "weight_kg", "kcal_target"):
        raw = data.get(k)
        if raw not in (None, ""):
            try:
                fields[k] = float(raw)
            except (TypeError, ValueError):
                pass
    w, h = fields.get("weight_kg"), fields.get("height_cm")
    if w and h:
        fields["bmi"] = round(w / ((h / 100) ** 2), 1)
    try:
        await upsert_profile(member, fields)
    except Exception as e:
        logger.error("Profile save error: %s", e)
        return web.json_response({"error": "Errore salvataggio"}, status=500)
    return web.json_response({"ok": True, "bmi": fields.get("bmi")})


async def _h_shopping(request):
    items = await get_shopping_list(include_checked=True)
    body = "<h2>Lista della spesa</h2>"
    if not items:
        body += "<div class='card muted'>Lista vuota. Chiedi a HARIA: «genera la spesa dal piano».</div>"
    else:
        total = sum(it["price"] for it in items if it.get("price") is not None)
        priced = sum(1 for it in items if it.get("price") is not None)
        if priced:
            body += (f"<div class='card'>Totale stimato: <span class='kcal'>€{total:.2f}</span> "
                     f"<span class='muted'>({priced}/{len(items)} voci con prezzo)</span></div>")
        by_cat = {}
        for it in items:
            by_cat.setdefault(it["category"] or "Varie", []).append(it)
        for cat, lst in by_cat.items():
            sub = sum(it["price"] for it in lst if it.get("price") is not None)
            sub_lbl = f" <span class='muted'>€{sub:.2f}</span>" if sub else ""
            body += f"<div class='card'><b>{_e(cat)}</b>{sub_lbl}<table>"
            for it in lst:
                chk = "checked" if it["checked"] else ""
                price = f"€{it['price']:.2f}" if it.get("price") is not None else ""
                style = "text-decoration:line-through;color:#999" if it["checked"] else ""
                body += (f"<tr><td><input type='checkbox' class='shopchk' data-id='{it['id']}' {chk}> "
                         f"<span style='{style}'>{_e(it['name'])}</span></td>"
                         f"<td class='muted'>{_e(it['qty'] or '')}</td>"
                         f"<td class='muted'>{price}</td></tr>")
            body += "</table></div>"
    body += """<script>
document.querySelectorAll('.shopchk').forEach(c=>c.addEventListener('change',async e=>{
  const id=e.target.dataset.id;e.target.disabled=true;
  try{await fetch('./api/shopping/toggle',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:parseInt(id)})});location.reload();}
  catch(err){e.target.disabled=false;}
}));
</script>"""
    return _page("Spesa", body)


async def _h_shopping_toggle(request):
    try:
        data = await request.json()
        ok = await toggle_shopping_item(int(data.get("id")))
    except Exception:
        return web.json_response({"error": "Richiesta non valida"}, status=400)
    return web.json_response({"ok": ok})


async def _h_pantry(request):
    items = await get_pantry()
    exp = await get_pantry_expiring(3)
    exp_names = {(i["name"], i["expires_on"]) for i in exp}
    body = "<h2>Dispensa / scorte</h2>"
    if exp:
        body += "<div class='card'><b>⚠️ In scadenza (≤ 3 giorni)</b><table>"
        for it in exp:
            body += f"<tr><td>{_e(it['name'])}</td><td class='muted'>{_e(it['qty'] or '')}</td><td class='muted'>scad. {_e(it['expires_on'])}</td></tr>"
        body += "</table></div>"
    if not items:
        body += "<div class='card muted'>Dispensa vuota. Chiedi a HARIA: «aggiungi alla dispensa…».</div>"
    else:
        by_cat = {}
        for it in items:
            by_cat.setdefault(it["category"] or "Varie", []).append(it)
        for cat, lst in by_cat.items():
            body += f"<div class='card'><b>{_e(cat)}</b><table>"
            for it in lst:
                warn = "⚠️ " if (it["name"], it["expires_on"]) in exp_names else ""
                scad = f"scad. {_e(it['expires_on'])}" if it["expires_on"] else ""
                body += f"<tr><td>{warn}{_e(it['name'])}</td><td class='muted'>{_e(it['qty'] or '')}</td><td class='muted'>{scad}</td></tr>"
            body += "</table></div>"
    return _page("Dispensa", body)


def _briefing_user_id() -> str | None:
    """chat_id (string) del primo utente: stesso user_id usato da Telegram per i briefing."""
    u = _chat_user()
    if not u or not u.get("chat_id"):
        return None
    return str(u["chat_id"])


def _topics_to_text(topics_json: str) -> str:
    """Serializza i topics in righe editabili: 'tema | sito1, sito2'."""
    lines = []
    for it in news._parse_topics(topics_json):
        line = it["topic"]
        if it.get("sources"):
            line += " | " + ", ".join(it["sources"])
        lines.append(line)
    return "\n".join(lines)


def _parse_topics_input(text: str) -> list[dict]:
    """Inverso di _topics_to_text: una riga per tema, '|' separa la whitelist siti."""
    items = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "|" in line:
            t, s = line.split("|", 1)
            sources = [x.strip().lower() for x in s.split(",") if x.strip()]
        else:
            t, sources = line, []
        t = t.strip()
        if t:
            items.append({"topic": t, "sources": sources})
    return items


def _briefing_form(b: dict | None) -> str:
    """Form add (b=None) o edit (b=briefing dict con topics già parsati in JSON)."""
    bid = b["id"] if b else ""
    cron = _e(b["cron"]) if b else ""
    topics_txt = _e(_topics_to_text(b["topics"])) if b else ""
    title = f"Briefing #{b['id']}" if b else "Nuovo briefing"
    del_btn = (f"<button type='button' class='btn brief-del' data-id='{b['id']}' "
               f"style='background:#c0392b;margin-left:8px'>Elimina</button>") if b else ""
    return (f"<form class='card brief-edit' data-id=\"{bid}\"><b>{title}</b><br>"
            "<div class='efield' style='display:block'><label>Temi (uno per riga; "
            "opzionale «| sito1, sito2» per limitare le fonti)</label>"
            f"<textarea name='topics' rows='4' style='width:100%;max-width:520px;"
            f"padding:6px;border:1px solid #ccc;border-radius:6px;font-size:14px'>{topics_txt}</textarea></div>"
            "<div class='efield'><label>Cron (min ora gg mese gg-sett)</label>"
            f"<input type='text' name='cron' value='{cron}' placeholder='0 8 * * *' style='width:160px'></div>"
            "<br><button class='btn' type='submit'>Salva</button>"
            f"{del_btn}<span class='muted savemsg' style='margin-left:10px'></span></form>")


async def _h_briefings(request):
    uid = _briefing_user_id()
    body = "<h2>Briefing notizie programmati</h2>"
    if not uid:
        body += "<div class='card muted'>Nessun utente configurato.</div>"
        return _page("Notizie", body)
    items = await get_user_briefings(uid)
    body += ("<div class='card muted'>Ogni briefing cerca i temi sul web e ti manda un riassunto "
             "via Telegram agli orari del cron. Es. cron <code>0 8 * * *</code> = ogni giorno alle 8:00.</div>")
    if not items:
        body += "<div class='card muted'>Nessun briefing configurato.</div>"
    for b in items:
        body += _briefing_form(b)
    body += "<h2>Aggiungi</h2>" + _briefing_form(None)
    body += """<script>
async function briefSave(f){
  const msg=f.querySelector('.savemsg'),btn=f.querySelector('button[type=submit]');
  const d={topics:f.querySelector('[name=topics]').value,cron:f.querySelector('[name=cron]').value};
  if(f.dataset.id)d.id=parseInt(f.dataset.id);
  btn.disabled=true;msg.textContent='…';
  try{const r=await fetch('./api/briefings/save',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(d)});const j=await r.json();
    if(j.ok){location.reload();}else{msg.textContent=j.error||'errore';btn.disabled=false;}}
  catch(e){msg.textContent='errore di rete';btn.disabled=false;}
}
document.querySelectorAll('.brief-edit').forEach(f=>f.addEventListener('submit',e=>{e.preventDefault();briefSave(f);}));
document.querySelectorAll('.brief-del').forEach(b=>b.addEventListener('click',async e=>{
  if(!confirm('Eliminare il briefing?'))return;b.disabled=true;
  try{await fetch('./api/briefings/delete',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:parseInt(b.dataset.id)})});location.reload();}
  catch(e){b.disabled=false;}
}));
</script>"""
    return _page("Notizie", body)


async def _h_briefings_save(request):
    uid = _briefing_user_id()
    if not uid:
        return web.json_response({"error": "Nessun utente configurato"}, status=503)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "JSON non valido"}, status=400)
    cron = (data.get("cron") or "").strip()
    topics = _parse_topics_input(data.get("topics") or "")
    if not topics:
        return web.json_response({"error": "Indica almeno un tema"}, status=400)
    if not cron:
        return web.json_response({"error": "Indica il cron"}, status=400)
    topics_json = news._dump_topics(topics)
    bid = data.get("id")
    if bid:
        b = await update_briefing(int(bid), uid, topics_json, cron)
        if not b:
            return web.json_response({"error": "Briefing non trovato"}, status=404)
        scheduler.cancel_briefing(b["id"])
        if not scheduler.schedule_briefing(b):
            return web.json_response({"error": "Cron non valido"}, status=400)
        return web.json_response({"ok": True, "id": b["id"]})
    b = await add_briefing(uid, topics_json, cron)
    if not scheduler.schedule_briefing(b):
        await deactivate_briefing(b["id"], uid)
        return web.json_response({"error": "Cron non valido"}, status=400)
    return web.json_response({"ok": True, "id": b["id"]})


async def _h_briefings_delete(request):
    uid = _briefing_user_id()
    if not uid:
        return web.json_response({"error": "Nessun utente configurato"}, status=503)
    try:
        data = await request.json()
        bid = int(data.get("id"))
    except Exception:
        return web.json_response({"error": "Richiesta non valida"}, status=400)
    ok = await deactivate_briefing(bid, uid)
    if ok:
        scheduler.cancel_briefing(bid)
    return web.json_response({"ok": ok})


async def _h_chat(request):
    body = """<h2>Chat con HARIA</h2>
<div class='card'>
<div id='log'></div>
<form id='cform'><input id='cin' autocomplete='off' placeholder='Scrivi a HARIA…' autofocus>
<button id='cbtn' type='submit'>Invia</button></form>
</div>
<script>
const log=document.getElementById('log'),form=document.getElementById('cform'),
  inp=document.getElementById('cin'),btn=document.getElementById('cbtn');
function add(text,cls){const d=document.createElement('div');d.className='msg '+cls;d.textContent=text;
  log.appendChild(d);d.scrollIntoView();return d;}
form.addEventListener('submit',async e=>{e.preventDefault();
  const msg=inp.value.trim();if(!msg)return;
  add(msg,'u');inp.value='';btn.disabled=true;
  const wait=add('…','a');
  try{const r=await fetch('./api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:msg})});
    const j=await r.json();wait.textContent=j.reply||j.error||'Errore';}
  catch(err){wait.textContent='Errore di rete';}
  btn.disabled=false;inp.focus();});
</script>"""
    return _page("Chat", body)


async def _h_chat_api(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "JSON non valido"}, status=400)
    message = (data.get("message") or "").strip()
    if not message:
        return web.json_response({"error": "Messaggio vuoto"}, status=400)
    user_cfg = _chat_user()
    if not user_cfg:
        return web.json_response({"error": "Nessun utente configurato"}, status=503)
    user_id = f"ha_chat_{user_cfg.get('chat_id', 'default')}"
    try:
        reply = await chat(user_id, message, user_cfg)
        return web.json_response({"reply": reply})
    except Exception as e:
        logger.error("Chat ingress error: %s", e)
        return web.json_response({"error": "Errore interno"}, status=500)


async def _h_export(request):
    end = date.today()
    start = end - timedelta(days=30)
    df = request.query.get("from") or start.isoformat()
    dt = request.query.get("to") or end.isoformat()
    rows = await export_meals(df, dt)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["data", "membro", "pasto", "descrizione", "kcal", "proteine_g", "carbo_g", "grassi_g", "registrato_da"])
    for r in rows:
        w.writerow([r["eaten_at"], r["member"], r["meal_type"], r["description"],
                    r["kcal_total"], r["protein_g"], r["carbs_g"], r["fat_g"], r["logged_by"]])
    return web.Response(
        body=buf.getvalue().encode("utf-8-sig"),
        content_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="haria_diario_{df}_{dt}.csv"'},
    )


def build_web_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _h_plan)
    app.router.add_get("/month", _h_month)
    app.router.add_get("/diary", _h_diary)
    app.router.add_get("/profiles", _h_profiles)
    app.router.add_get("/shopping", _h_shopping)
    app.router.add_post("/api/shopping/toggle", _h_shopping_toggle)
    app.router.add_post("/api/profile/save", _h_profile_save)
    app.router.add_get("/pantry", _h_pantry)
    app.router.add_get("/briefings", _h_briefings)
    app.router.add_post("/api/briefings/save", _h_briefings_save)
    app.router.add_post("/api/briefings/delete", _h_briefings_delete)
    app.router.add_get("/chat", _h_chat)
    app.router.add_post("/api/chat", _h_chat_api)
    app.router.add_get("/export.csv", _h_export)
    return app


async def start(port: int = 8099):
    app = build_web_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("Pannello web HARIA su porta %d (ingress).", port)
    return runner
