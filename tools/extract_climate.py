#!/usr/bin/env python3
"""Estrazione statistiche climatiche long-term da Home Assistant -> CSV.

Standalone (NON parte di HARIA). Si collega alla WebSocket API di HA, scarica le
statistiche orarie (mean/min/max) di tutti i sensori clima/aria per un intervallo
e scrive un CSV in formato long: timestamp_iso,entity,mean,min,max.

Uso:
    set HA_URL=http://homeassistant.local:8123      (PowerShell: $env:HA_URL=...)
    set HA_TOKEN=<long-lived access token>
    python extract_climate.py [--days 60] [--period hour] [--out climate.csv]

Il token NON viene salvato: letto solo da variabile d'ambiente.
"""
import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone

try:
    import websockets
except ImportError:
    sys.exit("Manca 'websockets'. Installa: pip install websockets")

ENTITIES = [
    # Temperature
    "sensor.smarther_thermostat_current_temperature",
    "sensor.tempera_balcone_temperature",
    "sensor.temperatura_balconcino_temperature",
    "sensor.temperatura_meteo",
    "sensor.temperatura_salone",
    "sensor.temperatura_cucina",
    "sensor.temperatura_corridoio",
    "sensor.temperatura_camera",
    "sensor.temperatura_cameretta",
    "sensor.temperatura_bagno_padronale",
    "sensor.temperatura_bagno_ospiti",
    # Umidità
    "sensor.smarther_thermostat_humidity_sensor",
    "sensor.timmerflotte_temp_hmd_sensor_humidity",
    "sensor.timmerflotte_temp_hmd_sensor_humidity_2",
    "sensor.umidita_meteo",
    "sensor.sensore_3_humidity",
    "sensor.alpstuga_air_quality_monitor_humidity_sensor",
    # Qualità aria
    "sensor.sensore_3_current_pm2_5",
    "sensor.alpstuga_air_quality_monitor_pm2_5_density",
    "sensor.sensore_3_voc_index",
    "sensor.co2_camera_da_letto",
    "sensor.camera_da_letto_alpstuga_co2",  # vecchia entità CO2 (solo raw ~10gg in stats)
]


def _ws_url(http_url: str) -> str:
    u = http_url.rstrip("/")
    if u.startswith("https://"):
        return "wss://" + u[len("https://"):] + "/api/websocket"
    if u.startswith("http://"):
        return "ws://" + u[len("http://"):] + "/api/websocket"
    return "ws://" + u + "/api/websocket"


async def fetch(url: str, token: str, days: int, period: str, out: str):
    start = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    end = datetime.now(timezone.utc).isoformat()
    ws_url = _ws_url(url)
    print(f"Connessione {ws_url} ...")
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        hello = json.loads(await ws.recv())
        if hello.get("type") != "auth_required":
            sys.exit(f"Handshake inatteso: {hello}")
        await ws.send(json.dumps({"type": "auth", "access_token": token}))
        auth = json.loads(await ws.recv())
        if auth.get("type") != "auth_ok":
            sys.exit(f"Auth fallita: {auth}")
        print("Auth OK. Richiesta statistiche ...")

        msg_id = 1
        await ws.send(json.dumps({
            "id": msg_id,
            "type": "recorder/statistics_during_period",
            "start_time": start,
            "end_time": end,
            "statistic_ids": ENTITIES,
            "period": period,
            "types": ["mean", "min", "max"],
        }))
        # attendi la risposta con l'id giusto
        while True:
            resp = json.loads(await ws.recv())
            if resp.get("id") == msg_id:
                break
        if not resp.get("success"):
            sys.exit(f"Errore statistiche: {resp}")

        result = resp["result"]
        rows = 0
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["timestamp_iso", "entity", "mean", "min", "max"])
            for entity, points in result.items():
                for p in points:
                    ts = p.get("start")
                    # HA può tornare start in ms epoch o ISO
                    if isinstance(ts, (int, float)):
                        ts_iso = datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat()
                    else:
                        ts_iso = str(ts)
                    w.writerow([ts_iso, entity, p.get("mean"), p.get("min"), p.get("max")])
                    rows += 1
        size = os.path.getsize(out)
        print(f"Scritte {rows} righe -> {out} ({size/1024:.0f} KB)")
        empty = [e for e in ENTITIES if not result.get(e)]
        if empty:
            print("Nessuna statistica per:", ", ".join(empty))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--period", default="hour", choices=["5minute", "hour", "day", "week", "month"])
    ap.add_argument("--out", default="climate.csv")
    args = ap.parse_args()

    url = os.environ.get("HA_URL", "http://homeassistant.local:8123")
    token = os.environ.get("HA_TOKEN")
    if not token:
        # fallback: file untracked accanto allo script (gitignored)
        tok_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ha_token.txt")
        if os.path.exists(tok_path):
            with open(tok_path, encoding="utf-8") as f:
                token = f.read().strip()
    if not token:
        sys.exit("Imposta HA_TOKEN (env) oppure crea tools/ha_token.txt con il long-lived token.")

    asyncio.run(fetch(url, token, args.days, args.period, args.out))


if __name__ == "__main__":
    main()
