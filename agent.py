"""Telemetry agent: collect metrics and push them to the server (FR-01..04)."""
import json
import os
import platform
import socket
import sys
import time

import psutil
import requests

DEFAULTS = {"node_id": None, "server": "http://127.0.0.1:5000", "interval": 5, "api_key": ""}


def load_config(path="config.json"):
    cfg = dict(DEFAULTS)
    try:
        with open(path) as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    # DNHBM_API_KEY env var wins over config.json, so the key need not live in a file.
    cfg["api_key"] = os.environ.get("DNHBM_API_KEY") or cfg["api_key"]
    cfg["node_id"] = cfg["node_id"] or socket.gethostname()
    if not isinstance(cfg["interval"], (int, float)) or cfg["interval"] <= 0:
        cfg["interval"] = DEFAULTS["interval"]
    return cfg


class Collector:
    """Bandwidth (Mbps) and packet loss are computed from deltas between samples."""

    def __init__(self):
        self.prev = psutil.net_io_counters()
        self.prev_t = time.time()
        psutil.cpu_percent(None)

    def sample(self):
        cur, t = psutil.net_io_counters(), time.time()
        dt = max(t - self.prev_t, 1e-6)
        bits = ((cur.bytes_sent - self.prev.bytes_sent) +
                (cur.bytes_recv - self.prev.bytes_recv)) * 8
        pkts = (cur.packets_sent - self.prev.packets_sent) + \
               (cur.packets_recv - self.prev.packets_recv)
        bad = (cur.errin - self.prev.errin) + (cur.errout - self.prev.errout) + \
              (cur.dropin - self.prev.dropin) + (cur.dropout - self.prev.dropout)
        self.prev, self.prev_t = cur, t
        try:
            conns = len(psutil.net_connections())
        except (psutil.AccessDenied, OSError):  # macOS needs root for this
            conns = 0
        return {
            "cpu_usage": psutil.cpu_percent(None),
            "memory_usage": psutil.virtual_memory().percent,
            "network_throughput": round(bits / dt / 1e6, 3),
            "packet_loss": round(min(100.0, 100.0 * bad / pkts), 3) if pkts else 0.0,
            "connection_count": conns,
        }


def auth_headers(cfg):
    return {"X-API-Key": cfg["api_key"]} if cfg["api_key"] else {}


def register(cfg):
    r = requests.post(cfg["server"] + "/api/register", timeout=5, headers=auth_headers(cfg), json={
        "node_id": cfg["node_id"], "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}", "interval": cfg["interval"]})
    r.raise_for_status()


def run(config_path="config.json"):
    cfg = load_config(config_path)              # Initialising
    collector = Collector()
    registered = False
    while True:
        started = time.time()
        try:
            if not registered:                  # Registering (retry until success)
                register(cfg)
                registered = True
            payload = {"node_id": cfg["node_id"], **collector.sample()}   # Sampling
            r = requests.post(cfg["server"] + "/api/metrics", json=payload,
                              headers=auth_headers(cfg), timeout=5)  # Transmitting
            if r.status_code == 404:            # server lost us; re-register
                registered = False
        except requests.RequestException as e:
            print(f"[{cfg['node_id']}] server unreachable: {e}", file=sys.stderr)
        time.sleep(max(0, cfg["interval"] - (time.time() - started)))   # Waiting


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "config.json")
