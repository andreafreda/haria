import json
import logging
import os
from pathlib import Path
from aiohttp import web
from claude_engine import chat

logger = logging.getLogger(__name__)

PORT = int(os.environ.get("INGRESS_PORT", 8099))
WEB_DIR = Path(__file__).parent / "web"
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/data/options.json")

# Ingress strips the path prefix — HA passes it via X-Ingress-Path header.
# We serve everything relative to that prefix at runtime.


def _load_users() -> dict[str, dict]:
    with open(CONFIG_PATH) as f:
        cfg = json.load(f)
    return {str(u["chat_id"]): u for u in cfg.get("users", [])}


def _ha_chat_user(cfg_path: str) -> dict | None:
    """Return first user marked for ha_chat, or first user if none marked."""
    with open(cfg_path) as f:
        cfg = json.load(f)
    users = cfg.get("users", [])
    return users[0] if users else None


async def _handle_index(request: web.Request) -> web.Response:
    index = WEB_DIR / "index.html"
    return web.Response(
        body=index.read_bytes(),
        content_type="text/html",
        charset="utf-8",
    )


async def _handle_static(request: web.Request) -> web.Response:
    filename = request.match_info["filename"]
    path = WEB_DIR / filename
    if not path.exists() or not path.is_file():
        raise web.HTTPNotFound()
    suffix = path.suffix.lower()
    content_types = {
        ".css": "text/css",
        ".js": "application/javascript",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }
    ct = content_types.get(suffix, "application/octet-stream")
    return web.Response(body=path.read_bytes(), content_type=ct)


async def _handle_chat(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    message = body.get("message", "").strip()
    if not message:
        return web.json_response({"error": "Empty message"}, status=400)

    user_cfg = _ha_chat_user(CONFIG_PATH)
    if not user_cfg:
        return web.json_response({"error": "Nessun utente configurato"}, status=503)

    user_id = f"ha_chat_{user_cfg.get('chat_id', 'default')}"
    try:
        reply = await chat(user_id, message, user_cfg)
        return web.json_response({"reply": reply})
    except Exception as e:
        logger.error("Chat error: %s", e)
        return web.json_response({"error": "Errore interno"}, status=500)


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", _handle_index)
    app.router.add_get("/{filename}", _handle_static)
    app.router.add_post("/api/chat", _handle_chat)
    return app


async def start_server():
    app = build_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Web chat server su porta %d", PORT)
    return runner
