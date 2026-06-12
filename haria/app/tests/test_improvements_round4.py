"""Round 4 migliorie — TASK 35-42."""
import claude_engine
import webpanel
import config as cfg


# ---- TASK 39: auth ingress middleware ----

class _FakeReq:
    def __init__(self, remote="1.2.3.4", headers=None):
        self.remote = remote
        self.headers = headers or {}


async def _ok_handler(request):
    return "OK"


async def test_ingress_blocca_remoto(monkeypatch):
    monkeypatch.setattr(cfg, "get", lambda k, d=None: True if k == "panel_auth" else d)
    resp = await webpanel._ingress_only(_FakeReq(), _ok_handler)
    assert resp.status == 401


async def test_ingress_ammette_header(monkeypatch):
    monkeypatch.setattr(cfg, "get", lambda k, d=None: True if k == "panel_auth" else d)
    r = _FakeReq(headers={"X-Ingress-Path": "/x"})
    assert await webpanel._ingress_only(r, _ok_handler) == "OK"


async def test_ingress_ammette_localhost(monkeypatch):
    monkeypatch.setattr(cfg, "get", lambda k, d=None: True if k == "panel_auth" else d)
    assert await webpanel._ingress_only(_FakeReq(remote="127.0.0.1"), _ok_handler) == "OK"


async def test_ingress_disattivato(monkeypatch):
    monkeypatch.setattr(cfg, "get", lambda k, d=None: False if k == "panel_auth" else d)
    assert await webpanel._ingress_only(_FakeReq(), _ok_handler) == "OK"


# ---- TASK 40: flag ha_chat / telegram / voice ----

def _routes(app):
    return {r.resource.canonical for r in app.router.routes()}


def test_ha_chat_false_no_route_chat(monkeypatch):
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {"ha_chat": False} if k == "modules" else d)
    routes = _routes(webpanel.build_web_app())
    assert "/api/chat" not in routes
    assert "/chat" not in routes


def test_ha_chat_true_route_chat(monkeypatch):
    monkeypatch.setattr(cfg, "get", lambda k, d=None: {"ha_chat": True} if k == "modules" else d)
    routes = _routes(webpanel.build_web_app())
    assert "/api/chat" in routes


async def test_fire_reminder_bot_none(monkeypatch):
    import scheduler
    called = []
    monkeypatch.setattr(scheduler, "_bot", None)
    monkeypatch.setattr(scheduler, "deactivate_reminder",
                        lambda *a, **k: called.append(a))
    # bot None: nessuna eccezione, reminder one-shot NON disattivato
    await scheduler._fire(1, "123", "msg", None)
    assert called == []


# ---- TASK 36: prompt cache TTL 1h ----

def test_get_tools_cache_ttl_1h():
    assert claude_engine.get_tools()[-1]["cache_control"]["ttl"] == "1h"


async def _cache_x():
    return "x"


async def test_build_system_cache_ttl_1h(db, monkeypatch):
    monkeypatch.setattr(claude_engine, "get_entity_cache", _cache_x)
    blocks = await claude_engine._build_system("123", {"name": "Test"})
    assert blocks[0]["cache_control"]["ttl"] == "1h"
