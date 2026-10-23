"""Telemetry agent: collect metrics and push them to the server (FR-01..04)."""
import json
import logging
import os
import platform
import socket
import sys
import time
from datetime import datetime, timezone

import psutil
import requests

DEFAULTS = {
    "node_id": None,
    "server": "https://dnhbm.onrender.com",
    "interval": 5,
    "api_key": "",
    "log_file": "agent.log",
    "metrics_log": "metrics.log",
    "offline_queue": "offline_queue.jsonl",
}

logger = logging.getLogger("agent")


def setup_logging(log_path=None):
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(formatter)
    logger.addHandler(sh)
    if log_path:
        try:
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except OSError as e:
            logger.warning(f"Could not open log file {log_path}: {e}")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def load_config(path_or_node="config.json"):
    """Load configuration from JSON file, dictionary, CLI argument, or environment variables."""
    cfg = dict(DEFAULTS)
    if isinstance(path_or_node, dict):
        cfg.update(path_or_node)
    elif os.path.isfile(path_or_node):
        try:
            with open(path_or_node, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to read config from {path_or_node}: {e}")
    elif path_or_node and not path_or_node.endswith(".json"):
        # Convenience: user passed node_id directly as argument, e.g. python agent.py NODE-02
        cfg["node_id"] = path_or_node

    # Environment variable overrides
    if os.environ.get("DNHBM_API_KEY"):
        cfg["api_key"] = os.environ["DNHBM_API_KEY"]
    if os.environ.get("NETMON_NODE_ID"):
        cfg["node_id"] = os.environ["NETMON_NODE_ID"]
    if os.environ.get("NETMON_SERVER"):
        cfg["server"] = os.environ["NETMON_SERVER"]
    if os.environ.get("NETMON_INTERVAL"):
        try:
            cfg["interval"] = float(os.environ["NETMON_INTERVAL"])
        except ValueError:
            pass

    cfg["node_id"] = cfg["node_id"] or socket.gethostname()
    if not isinstance(cfg["interval"], (int, float)) or cfg["interval"] <= 0:
        cfg["interval"] = DEFAULTS["interval"]

    # If running multiple nodes concurrently, isolate local log and queue files per node
    nid = cfg["node_id"]
    if nid not in ("NODE-01", socket.gethostname()):
        if cfg.get("log_file") == DEFAULTS["log_file"]:
            cfg["log_file"] = f"agent_{nid}.log"
        if cfg.get("metrics_log") == DEFAULTS["metrics_log"]:
            cfg["metrics_log"] = f"metrics_{nid}.log"
        if cfg.get("offline_queue") == DEFAULTS["offline_queue"]:
            cfg["offline_queue"] = f"offline_queue_{nid}.jsonl"

    return cfg


def auth_headers(cfg):
    return {"X-API-Key": cfg["api_key"]} if cfg.get("api_key") else {}


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
                (cur.bytes_recv - self.prev.recv if hasattr(cur, "recv") else cur.bytes_recv - self.prev.bytes_recv)) * 8
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


def append_offline_log(queue_path, payload):
    """Buffer an unsent telemetry record to the local offline queue file."""
    try:
        with open(queue_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError as e:
        logger.error(f"Failed to buffer offline telemetry log: {e}")


def load_offline_queue(queue_path):
    """Read pending offline logs from disk."""
    if not os.path.exists(queue_path):
        return []
    records = []
    try:
        with open(queue_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except OSError as e:
        logger.error(f"Failed to read offline queue file: {e}")
    return records


def clear_offline_queue(queue_path):
    """Remove offline queue file after successful flush."""
    try:
        if os.path.exists(queue_path):
            os.remove(queue_path)
    except OSError as e:
        logger.error(f"Failed to clear offline queue file: {e}")


def flush_offline_queue(cfg):
    """Send all locally buffered offline telemetry logs to the central server."""
    queue_path = cfg.get("offline_queue", "offline_queue.jsonl")
    records = load_offline_queue(queue_path)
    if not records:
        return 0
    logger.info(f"[{cfg['node_id']}] Flushing {len(records)} buffered offline telemetry logs to {cfg['server']}...")
    try:
        r = requests.post(cfg["server"] + "/api/metrics", json=records, headers=auth_headers(cfg), timeout=10)
        if r.status_code == 201:
            clear_offline_queue(queue_path)
            logger.info(f"[{cfg['node_id']}] Successfully flushed {len(records)} offline logs.")
            return len(records)
        elif r.status_code == 404:
            logger.warning(f"[{cfg['node_id']}] Node unrecognised by server during flush; re-register required.")
            return -1
    except requests.RequestException as e:
        logger.warning(f"[{cfg['node_id']}] Could not flush offline queue: {e}")
        return 0

    # Fall back to individual item push if batch rejected
    remaining = []
    sent = 0
    for rec in records:
        try:
            res = requests.post(cfg["server"] + "/api/metrics", json=rec, headers=auth_headers(cfg), timeout=5)
            if res.status_code == 201:
                sent += 1
            else:
                remaining.append(rec)
        except requests.RequestException:
            remaining.append(rec)

    if remaining:
        try:
            with open(queue_path, "w", encoding="utf-8") as f:
                for rec in remaining:
                    f.write(json.dumps(rec) + "\n")
        except OSError as e:
            logger.error(f"Failed to update offline queue: {e}")
    else:
        clear_offline_queue(queue_path)
    return sent


def log_metric_locally(metrics_log_path, payload):
    """Append metric record to persistent local offline log file."""
    try:
        with open(metrics_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError as e:
        logger.error(f"Failed to append to local metrics log: {e}")


def register(cfg):
    r = requests.post(cfg["server"] + "/api/register", timeout=5, headers=auth_headers(cfg), json={
        "node_id": cfg["node_id"], "hostname": socket.gethostname(),
        "os": f"{platform.system()} {platform.release()}", "interval": cfg["interval"]})
    r.raise_for_status()


def run(config_or_path="config.json"):
    cfg = config_or_path if isinstance(config_or_path, dict) else load_config(config_or_path)
    setup_logging(cfg.get("log_file", "agent.log"))
    logger.info(f"[{cfg['node_id']}] Agent started. Reporting to {cfg['server']} every {cfg['interval']}s.")
    collector = Collector()
    registered = False
    queue_path = cfg.get("offline_queue", "offline_queue.jsonl")
    metrics_log_path = cfg.get("metrics_log", "metrics.log")

    while True:
        started = time.time()
        ts = now_iso()
        sample = collector.sample()
        payload = {"node_id": cfg["node_id"], "timestamp": ts, **sample}

        # Save to local offline metrics log file (always available offline)
        log_metric_locally(metrics_log_path, payload)
        logger.info(f"[{cfg['node_id']}] Metric sampled: CPU={payload['cpu_usage']}%, Mem={payload['memory_usage']}%, "
                    f"BW={payload['network_throughput']}Mbps, Loss={payload['packet_loss']}%, Conns={payload['connection_count']}")

        try:
            if not registered:                  # Registering (retry until success)
                register(cfg)
                registered = True
                logger.info(f"[{cfg['node_id']}] Node registered successfully.")
                # When newly connected, flush any queued offline logs
                flush_offline_queue(cfg)

            r = requests.post(cfg["server"] + "/api/metrics", json=payload, headers=auth_headers(cfg), timeout=5)  # Transmitting
            if r.status_code == 201:
                # Transmit success; also check if any backlog remains
                if os.path.exists(queue_path):
                    flush_offline_queue(cfg)
            elif r.status_code == 404:            # server lost us; re-register and buffer
                registered = False
                logger.warning(f"[{cfg['node_id']}] Server returned 404. Buffering telemetry log offline.")
                append_offline_log(queue_path, payload)
            else:
                logger.warning(f"[{cfg['node_id']}] Server error {r.status_code}. Buffering telemetry log offline.")
                append_offline_log(queue_path, payload)
        except requests.RequestException as e:
            registered = False
            logger.warning(f"[{cfg['node_id']}] Server unreachable: {e}. Buffered telemetry log offline in {queue_path}.")
            append_offline_log(queue_path, payload)

        time.sleep(max(0, cfg["interval"] - (time.time() - started)))   # Waiting


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    server_override = sys.argv[2] if len(sys.argv) > 2 else None
    config = load_config(target)
    if server_override:
        config["server"] = server_override
    run(config)
