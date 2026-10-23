"""Central server: REST API + SQLite + dashboard host (FR-01, 05-13)."""
import csv
import hmac
import io
import os
import sqlite3
import time
from datetime import datetime, timezone

from flask import Flask, Response, g, jsonify, request, send_from_directory

HERE = os.path.dirname(os.path.abspath(__file__))
METRIC_FIELDS = ["cpu_usage", "memory_usage", "network_throughput",
                 "packet_loss", "connection_count"]
# Accepted aliases from the test data in Lab-5 (cpu, mem, bandwidth, ...)
ALIASES = {"cpu": "cpu_usage", "mem": "memory_usage",
           "bandwidth": "network_throughput", "connections": "connection_count"}
RANGES = {"cpu_usage": (0, 100), "memory_usage": (0, 100), "packet_loss": (0, 100),
          "network_throughput": (0, None), "connection_count": (0, None)}
DEFAULT_INTERVAL = 5
FRESH_INTERVALS = 3  # node is offline when last push older than 3 intervals

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY, hostname TEXT, os TEXT,
    interval_s REAL DEFAULT 5, registered_at TEXT, last_seen TEXT);
CREATE TABLE IF NOT EXISTS metrics (
    metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL REFERENCES nodes(node_id), timestamp TEXT NOT NULL,
    cpu_usage REAL, memory_usage REAL, network_throughput REAL,
    packet_loss REAL, connection_count INTEGER);
CREATE INDEX IF NOT EXISTS idx_metrics_node_ts ON metrics(node_id, timestamp);
CREATE TABLE IF NOT EXISTS thresholds (
    threshold_id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type TEXT NOT NULL UNIQUE, limit_value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL REFERENCES nodes(node_id),
    threshold_id INTEGER NOT NULL REFERENCES thresholds(threshold_id),
    message TEXT, timestamp TEXT NOT NULL, is_active INTEGER DEFAULT 1);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def create_app(db_path=None, api_key=None):
    app = Flask(__name__)
    app.config["DB_PATH"] = db_path or os.environ.get("DB_PATH") or os.path.join(HERE, "monitor.db")
    # When an API key is set, every POST must send it in the X-API-Key header.
    # Unset (local demo, tests) means writes are open, as before.
    app.config["API_KEY"] = api_key if api_key is not None else os.environ.get("DNHBM_API_KEY", "")

    @app.before_request
    def require_api_key():
        key = app.config["API_KEY"]
        if key and request.method == "POST" and not hmac.compare_digest(
                request.headers.get("X-API-Key", ""), key):
            return jsonify({"error": "missing or wrong X-API-Key"}), 401

    def db():
        if "db" not in g:
            g.db = sqlite3.connect(app.config["DB_PATH"], timeout=30)
            g.db.row_factory = sqlite3.Row
            g.db.execute("PRAGMA foreign_keys = ON")
        return g.db

    @app.teardown_appcontext
    def close_db(_):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    with sqlite3.connect(app.config["DB_PATH"]) as init:
        init.execute("PRAGMA journal_mode = WAL")  # concurrent agents (TC13)
        init.executescript(SCHEMA)

    def err(msg, code=400):
        return jsonify({"error": msg}), code

    def is_num(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    # ---- FR-01: registration ----
    @app.post("/api/register")
    def register():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return err("JSON object required")
        node_id = data.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            return err("node_id (non-empty string) is required")
        interval = data.get("interval", DEFAULT_INTERVAL)
        if not is_num(interval) or interval <= 0:
            return err("interval must be a positive number")
        ts = now_iso()
        d = db()
        existing = d.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone()
        if existing:  # TC02: update, never duplicate
            d.execute("UPDATE nodes SET hostname=?, os=?, interval_s=? WHERE node_id=?",
                      (data.get("hostname"), data.get("os"), interval, node_id))
        else:
            d.execute("INSERT INTO nodes VALUES (?,?,?,?,?,NULL)",
                      (node_id, data.get("hostname"), data.get("os"), interval, ts))
        d.commit()
        return jsonify({"status": "updated" if existing else "registered",
                        "node_id": node_id}), (200 if existing else 201)

    # ---- FR-05/06/07/08: ingestion ----
    @app.post("/api/metrics")
    def ingest():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return err("JSON object required")
        data = {ALIASES.get(k, k): v for k, v in data.items()}
        node_id = data.get("node_id")
        if not isinstance(node_id, str) or not node_id.strip():
            return err("node_id (non-empty string) is required")
        values = {}
        for f in METRIC_FIELDS:
            v = data.get(f)
            if not is_num(v):
                return err(f"{f} is required and must be a number")
            lo, hi = RANGES[f]
            if v < lo or (hi is not None and v > hi):
                return err(f"{f} out of range")
            values[f] = v
        d = db()
        if not d.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone():
            return err("unknown node; register first", 404)
        ts = now_iso()
        d.execute("INSERT INTO metrics (node_id,timestamp,cpu_usage,memory_usage,"
                  "network_throughput,packet_loss,connection_count) VALUES (?,?,?,?,?,?,?)",
                  (node_id, ts, *[values[f] for f in METRIC_FIELDS]))
        d.execute("UPDATE nodes SET last_seen=? WHERE node_id=?", (ts, node_id))
        raised = []
        for t in d.execute("SELECT * FROM thresholds").fetchall():
            val = values.get(t["metric_type"])
            if val is not None and val > t["limit_value"]:
                msg = (f"{node_id}: {t['metric_type']} {val} exceeds "
                       f"threshold {t['limit_value']}")
                d.execute("INSERT INTO alerts (node_id,threshold_id,message,timestamp) "
                          "VALUES (?,?,?,?)", (node_id, t["threshold_id"], msg, ts))
                raised.append(msg)
        d.commit()
        return jsonify({"status": "stored", "timestamp": ts, "alerts": raised}), 201

    # ---- FR-09/10: metric queries ----
    @app.get("/api/metrics")
    def query_metrics():
        node_id = request.args.get("node_id")
        try:
            minutes = float(request.args.get("minutes", 60))
            limit = min(int(request.args.get("limit", 1000)), 10000)
        except ValueError:
            return err("minutes/limit must be numbers")
        since = datetime.fromtimestamp(time.time() - minutes * 60, timezone.utc).isoformat()
        sql, args = "SELECT * FROM metrics WHERE timestamp >= ?", [since]
        if node_id:
            sql += " AND node_id = ?"
            args.append(node_id)
        sql += " ORDER BY timestamp DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in db().execute(sql, args)]
        return jsonify(list(reversed(rows)))

    # ---- FR-11: node status ----
    @app.get("/api/nodes")
    def nodes():
        out = []
        for r in db().execute("SELECT * FROM nodes ORDER BY node_id"):
            n = dict(r)
            if n["last_seen"]:
                age = time.time() - datetime.fromisoformat(n["last_seen"]).timestamp()
                n["age_seconds"] = round(age, 1)
                n["status"] = "online" if age <= FRESH_INTERVALS * n["interval_s"] else "offline"
            else:
                n["age_seconds"], n["status"] = None, "registered"
            out.append(n)
        return jsonify(out)

    # ---- FR-12: alerts ----
    @app.get("/api/alerts")
    def alerts():
        # Alerts are never deleted by the server. ?minutes=30 returns only alerts from the
        # last 30 minutes; without it, the newest ?limit alerts are returned.
        limit = min(int(request.args.get("limit", 100)), 1000)
        sql, args = "SELECT * FROM alerts", []
        if request.args.get("minutes") is not None:
            try:
                minutes = float(request.args["minutes"])
            except ValueError:
                return err("minutes must be a number")
            since = datetime.fromtimestamp(time.time() - minutes * 60, timezone.utc)
            sql += " WHERE timestamp >= ?"
            args.append(since.isoformat(timespec="milliseconds"))
        sql += " ORDER BY alert_id DESC LIMIT ?"
        args.append(limit)
        return jsonify([dict(r) for r in db().execute(sql, args)])

    @app.get("/api/alerts/export")
    def export_alerts():
        # Full alert history as CSV, so it can be saved on your PC and looked at later.
        rows = db().execute("SELECT alert_id, node_id, threshold_id, timestamp, is_active, message "
                            "FROM alerts ORDER BY alert_id").fetchall()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["alert_id", "node_id", "threshold_id", "timestamp", "is_active", "message"])
        w.writerows(tuple(r) for r in rows)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": "attachment; filename=alerts.csv"})

    @app.post("/api/alerts/clear")
    def clear_alerts():
        db().execute("UPDATE alerts SET is_active=0")
        db().commit()
        return jsonify({"status": "cleared"})

    # ---- FR-13: thresholds ----
    @app.get("/api/thresholds")
    def get_thresholds():
        return jsonify([dict(r) for r in db().execute("SELECT * FROM thresholds")])

    @app.post("/api/thresholds")
    def set_threshold():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return err("JSON object required")
        mt = ALIASES.get(data.get("metric_type"), data.get("metric_type"))
        limit = data.get("limit_value")
        if mt not in METRIC_FIELDS:
            return err(f"metric_type must be one of {METRIC_FIELDS}")
        if not is_num(limit) or limit < 0:
            return err("limit_value must be a non-negative number")
        d = db()
        d.execute("INSERT INTO thresholds (metric_type,limit_value) VALUES (?,?) "
                  "ON CONFLICT(metric_type) DO UPDATE SET limit_value=excluded.limit_value",
                  (mt, limit))
        d.commit()
        return jsonify({"status": "saved", "metric_type": mt, "limit_value": limit}), 200

    @app.get("/dashboard")
    def dashboard():
        return send_from_directory(os.path.join(HERE, "static"), "dashboard.html")

    @app.get("/")
    def index():
        return dashboard()

    return app


if __name__ == "__main__":
    # Local default is 127.0.0.1:5000. On a host (Render, Docker, LAN) set HOST=0.0.0.0 and PORT.
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"),
                     port=int(os.environ.get("PORT", 5000)), threaded=True)
