"""Pannello web HARIA (ingress HA, porta 8099).

Dashboard sola-lettura:
  - Piano pasti settimanale (cosa cucinare)
  - Diario per membro (cosa ha mangiato + kcal)
  - Profili + peso/BMI
  - Lista della spesa
"""
import logging
from datetime import date, timedelta
from aiohttp import web
import csv
import io
from memory import (
    get_meal_plan, list_profiles, list_members_with_meals,
    get_meals, get_day_totals, get_hydration_day, get_shopping_list,
    get_weight_history, export_meals, get_profile,
    get_pantry, get_pantry_expiring,
)
from modules.food_diary import compute_macro_targets
import config as cfg
from claude_engine import chat

logger = logging.getLogger(__name__)


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
"""


def _page(title: str, body: str) -> web.Response:
    html = f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HARIA — {title}</title><style>{_CSS}</style></head><body>
<header>🤖 HARIA — Diario Alimentare</header>
<nav><a href="./">Piano</a><a href="./diary">Diario</a><a href="./profiles">Profili</a><a href="./shopping">Spesa</a><a href="./pantry">Dispensa</a><a href="./chat">Chat</a><a href="./export.csv">Export CSV</a></nav>
<main>{body}</main></body></html>"""
    return web.Response(text=html, content_type="text/html")


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
        iso = d.isoformat()
        meals = sorted(by_day.get(iso, []),
                       key=lambda m: (_MEAL_ORDER.get(m["meal_type"], 9), m.get("member") or ""))
        body += f"<div class='card'><b>{_GIORNI[i]} {d.strftime('%d/%m')}</b>"
        if not meals:
            body += " <span class='muted'>— niente pianificato</span>"
        else:
            day_kcal = sum(m["kcal"] or 0 for m in meals if not (m.get("member") or "").strip())
            if day_kcal:
                body += f" <span class='kcal'>{round(day_kcal)} kcal</span>"
            body += "<table><tr><th>Pasto</th><th>Chi</th><th>Portate</th><th>Ricetta</th><th>Porz.</th><th>kcal</th></tr>"
            for m in meals:
                who = (m.get("member") or "").strip()
                who_lbl = who.capitalize() if who else "<span class='muted'>comune</span>"
                body += (f"<tr><td>{m['meal_type']}</td><td>{who_lbl}</td><td>{m['items'] or ''}</td>"
                         f"<td class='muted'>{m['recipe'] or ''}</td><td>{m['servings'] or ''}</td>"
                         f"<td>{round(m['kcal']) if m['kcal'] else ''}</td></tr>")
            body += "</table>"
        body += "</div>"
    return _page("Piano", body)


async def _h_diary(request):
    day = request.query.get("date") or date.today().isoformat()
    members = await list_members_with_meals(day)
    body = f"<h2>Diario del {day}</h2>"
    if not members:
        body += "<div class='card muted'>Nessun pasto registrato in questa data.</div>"
    for member in members:
        meals = await get_meals(member, day + "T00:00:00", day + "T23:59:59")
        totals = await get_day_totals(member, day)
        hydr = await get_hydration_day(member, day)
        p = await get_profile(member)
        kcal_t = p.get("kcal_target") if p else None
        macros = compute_macro_targets(kcal_t, p.get("weight_kg")) if p else None
        body += (f"<div class='card'><b>{member.capitalize()}</b> "
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
        if meals:
            body += "<table><tr><th>Pasto</th><th>Descrizione</th><th>kcal</th></tr>"
            for m in sorted(meals, key=lambda x: _MEAL_ORDER.get(x['meal_type'], 9)):
                body += f"<tr><td>{m['meal_type']}</td><td>{m['description']}</td><td>{m['kcal_total'] or ''}</td></tr>"
            body += "</table>"
        body += "</div>"
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
            body += (f"<tr><td>{p['member']}</td><td>{p['sex'] or ''}</td><td>{p['age'] or ''}</td>"
                     f"<td>{p['height_cm'] or ''}</td><td>{p['weight_kg'] or ''}</td><td>{p['bmi'] or ''}</td>"
                     f"<td>{p['goal'] or ''}</td><td>{p['activity_level'] or ''}</td><td>{p['kcal_target'] or ''}</td>"
                     f"<td>{pt}</td><td>{ct}</td><td>{gt}</td></tr>")
        body += "</table>"
        for p in profs:
            hist = await get_weight_history(p["member"], limit=10)
            if hist:
                body += f"<div class='card'><b>Peso {p['member']}</b><table><tr><th>Data</th><th>kg</th><th>BMI</th></tr>"
                for h in hist:
                    body += f"<tr><td>{h['logged_at']}</td><td>{h['weight_kg']}</td><td>{h['bmi'] or ''}</td></tr>"
                body += "</table></div>"
    return _page("Profili", body)


async def _h_shopping(request):
    items = await get_shopping_list(include_checked=True)
    body = "<h2>Lista della spesa</h2>"
    if not items:
        body += "<div class='card muted'>Lista vuota. Chiedi a HARIA: «genera la spesa dal piano».</div>"
    else:
        by_cat = {}
        for it in items:
            by_cat.setdefault(it["category"] or "Varie", []).append(it)
        for cat, lst in by_cat.items():
            body += f"<div class='card'><b>{cat}</b><table>"
            for it in lst:
                mark = "<span class='chk'>✔</span> " if it["checked"] else ""
                body += f"<tr><td>{mark}{it['name']}</td><td class='muted'>{it['qty'] or ''}</td></tr>"
            body += "</table></div>"
    return _page("Spesa", body)


async def _h_pantry(request):
    items = await get_pantry()
    exp = await get_pantry_expiring(3)
    exp_names = {(i["name"], i["expires_on"]) for i in exp}
    body = "<h2>Dispensa / scorte</h2>"
    if exp:
        body += "<div class='card'><b>⚠️ In scadenza (≤ 3 giorni)</b><table>"
        for it in exp:
            body += f"<tr><td>{it['name']}</td><td class='muted'>{it['qty'] or ''}</td><td class='muted'>scad. {it['expires_on']}</td></tr>"
        body += "</table></div>"
    if not items:
        body += "<div class='card muted'>Dispensa vuota. Chiedi a HARIA: «aggiungi alla dispensa…».</div>"
    else:
        by_cat = {}
        for it in items:
            by_cat.setdefault(it["category"] or "Varie", []).append(it)
        for cat, lst in by_cat.items():
            body += f"<div class='card'><b>{cat}</b><table>"
            for it in lst:
                warn = "⚠️ " if (it["name"], it["expires_on"]) in exp_names else ""
                scad = f"scad. {it['expires_on']}" if it["expires_on"] else ""
                body += f"<tr><td>{warn}{it['name']}</td><td class='muted'>{it['qty'] or ''}</td><td class='muted'>{scad}</td></tr>"
            body += "</table></div>"
    return _page("Dispensa", body)


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
    app.router.add_get("/diary", _h_diary)
    app.router.add_get("/profiles", _h_profiles)
    app.router.add_get("/shopping", _h_shopping)
    app.router.add_get("/pantry", _h_pantry)
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
