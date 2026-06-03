import json
import os

_CONFIG_PATHS = [
    "/config/haria_options.json",
    "/data/options.json",
    os.environ.get("CONFIG_PATH", ""),
]

_cfg: dict = {}


def load() -> dict:
    global _cfg
    if _cfg:
        return _cfg
    for path in _CONFIG_PATHS:
        if path and os.path.exists(path):
            with open(path) as f:
                _cfg = json.load(f)
            return _cfg
    return _cfg


def get(key: str, default=None):
    return load().get(key, default)
