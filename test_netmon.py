"""Automated versions of the Lab-5 test cases (TC numbers in test names)."""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

from server import create_app

GOOD = {"node_id": "NODE-01", "cpu_usage": 42.5, "memory_usage": 61.0,
        "network_throughput": 3.4, "packet_loss": 0.2, "connection_count": 38}


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "t.db")
    app = create_app(path)
    c = app.test_client()
    c.post("/api/register", json={"node_id": "NODE-01", "hostname": "lab-pc-01", "os": "Windows 11"})
    return c, path


def rows(path, sql):
    with sqlite3.connect(path) as d:
        return d.execute(sql).fetchall()


def test_tc01_register_valid(tmp_path):
    path = str(tmp_path / "t.db")
    c = create_app(path).test_client()
    r = c.post("/api/register", json={"node_id": "NODE-01", "hostname": "lab-pc-01", "os": "Windows 11"})
    assert r.status_code == 201
    assert rows(path, "SELECT node_id, hostname, os FROM nodes") == [("NODE-01", "lab-pc-01", "Windows 11")]


def test_tc02_register_duplicate(env):
    c, path = env
    r = c.post("/api/register", json={"node_id": "NODE-01", "hostname": "new-host"})
    assert r.status_code == 200
    assert rows(path, "SELECT hostname FROM nodes") == [("new-host",)]


def test_tc03_valid_push(env):
    c, path = env
    r = c.post("/api/metrics", json=GOOD)
    assert r.status_code == 201 and r.get_json()["timestamp"]
    assert rows(path, "SELECT COUNT(*) FROM metrics") == [(1,)]
    assert rows(path, "SELECT last_seen IS NOT NULL FROM nodes") == [(1,)]


def test_tc03_lab_alias_fields(env):
    c, _ = env
    r = c.post("/api/metrics", json={"node_id": "NODE-01", "cpu": 42.5, "mem": 61.0,
                                     "bandwidth": 3.4, "packet_loss": 0.2, "connections": 38})
    assert r.status_code == 201


def test_tc04_invalid_type(env):
    c, path = env
    r = c.post("/api/metrics", json={**GOOD, "cpu_usage": "high", "memory_usage": None})
    assert r.status_code == 400
    assert rows(path, "SELECT COUNT(*) FROM metrics") == [(0,)]


def test_tc04_out_of_range_and_bool(env):
    c, _ = env
    assert c.post("/api/metrics", json={**GOOD, "cpu_usage": 150}).status_code == 400
    assert c.post("/api/metrics", json={**GOOD, "cpu_usage": True}).status_code == 400


def test_tc05_missing_node_id(env):
    c, path = env
    body = {k: v for k, v in GOOD.items() if k != "node_id"}
    assert c.post("/api/metrics", json=body).status_code == 400
    assert rows(path, "SELECT COUNT(*) FROM metrics") == [(0,)]


def test_unregistered_node_rejected(env):
    c, _ = env
    assert c.post("/api/metrics", json={**GOOD, "node_id": "GHOST"}).status_code == 404


def test_non_json_body(env):
    c, _ = env
    assert c.post("/api/metrics", data="not json").status_code == 400


def test_api_key_required_for_writes(tmp_path):
    # Deployed server: POSTs need the key, GETs (dashboard reads) stay open.
    c = create_app(str(tmp_path / "k.db"), api_key="s3cret").test_client()
    assert c.post("/api/register", json={"node_id": "NODE-01"}).status_code == 401
    assert c.post("/api/register", json={"node_id": "NODE-01"},
                  headers={"X-API-Key": "wrong"}).status_code == 401
    ok = {"X-API-Key": "s3cret"}
    assert c.post("/api/register", json={"node_id": "NODE-01"}, headers=ok).status_code == 201
    assert c.post("/api/metrics", json=GOOD, headers=ok).status_code == 201
    assert c.get("/api/nodes").status_code == 200


def test_tc07_threshold_create_update(env):
    c, _ = env
    assert c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": 80}).status_code == 200
    c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": 70})
    t = c.get("/api/thresholds").get_json()
    assert len(t) == 1 and t[0]["limit_value"] == 70


def test_tc07_threshold_invalid(env):
    c, _ = env
    assert c.post("/api/thresholds", json={"metric_type": "bogus", "limit_value": 5}).status_code == 400
    assert c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": "x"}).status_code == 400
    assert c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": -1}).status_code == 400


def test_tc08_alert_on_breach(env):
    c, _ = env
    c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": 50})
    r = c.post("/api/metrics", json={**GOOD, "cpu_usage": 92.0})
    assert len(r.get_json()["alerts"]) == 1
    a = c.get("/api/alerts").get_json()
    assert len(a) == 1 and a[0]["node_id"] == "NODE-01" and a[0]["is_active"] == 1


def test_alerts_kept_and_filtered_by_minutes(env):
    c, path = env
    c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": 50})
    c.post("/api/metrics", json={**GOOD, "cpu_usage": 92.0})
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat(timespec="milliseconds")
    with sqlite3.connect(path) as d:
        d.execute("INSERT INTO alerts (node_id,threshold_id,message,timestamp) "
                  "VALUES ('NODE-01',1,'old alert',?)", (old,))
    assert len(c.get("/api/alerts?minutes=30").get_json()) == 1   # only the fresh one
    assert len(c.get("/api/alerts").get_json()) == 2              # nothing deleted
    assert c.get("/api/alerts?minutes=abc").status_code == 400


def test_alert_history_export_csv(env):
    c, path = env
    c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": 50})
    c.post("/api/metrics", json={**GOOD, "cpu_usage": 92.0})
    r = c.get("/api/alerts/export")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    lines = r.get_data(as_text=True).strip().splitlines()
    assert lines[0] == "alert_id,node_id,threshold_id,timestamp,is_active,message"
    assert len(lines) == 2 and "NODE-01" in lines[1] and "cpu_usage 92.0" in lines[1]


def test_tc09_no_alert_below_threshold(env):
    c, _ = env
    c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": 80})
    c.post("/api/metrics", json={**GOOD, "cpu_usage": 35.0})
    assert c.get("/api/alerts").get_json() == []


def test_tc11_history_range(env):
    c, path = env
    c.post("/api/metrics", json=GOOD)
    old = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    with sqlite3.connect(path) as d:
        d.execute("INSERT INTO metrics (node_id,timestamp,cpu_usage,memory_usage,network_throughput,"
                  "packet_loss,connection_count) VALUES ('NODE-01',?,1,1,1,0,1)", (old,))
    assert len(c.get("/api/metrics?node_id=NODE-01&minutes=60").get_json()) == 1
    assert len(c.get("/api/metrics?node_id=NODE-01&minutes=300").get_json()) == 2


def test_tc12_node_status(env):
    c, path = env
    assert c.get("/api/nodes").get_json()[0]["status"] == "registered"
    c.post("/api/metrics", json=GOOD)
    assert c.get("/api/nodes").get_json()[0]["status"] == "online"
    stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()  # > 3 x 5s
    with sqlite3.connect(path) as d:
        d.execute("UPDATE nodes SET last_seen=?", (stale,))
    assert c.get("/api/nodes").get_json()[0]["status"] == "offline"


def test_tc13_concurrent_ingestion(tmp_path):
    path = str(tmp_path / "t.db")
    app = create_app(path)
    n_nodes, per_node = 10, 20
    errors = []

    def agent(i):
        c = app.test_client()
        nid = f"NODE-{i:02d}"
        c.post("/api/register", json={"node_id": nid})
        for _ in range(per_node):
            r = c.post("/api/metrics", json={**GOOD, "node_id": nid})
            if r.status_code != 201:
                errors.append(r.status_code)

    ts = [threading.Thread(target=agent, args=(i,)) for i in range(n_nodes)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert errors == []
    assert rows(path, "SELECT COUNT(*) FROM metrics") == [(n_nodes * per_node,)]
    assert rows(path, "SELECT COUNT(*) FROM nodes") == [(n_nodes,)]


def test_batch_metrics_ingestion(env):
    c, path = env
    batch = [
        {**GOOD, "timestamp": "2026-10-23T10:00:00.000Z", "cpu_usage": 10.0},
        {**GOOD, "timestamp": "2026-10-23T10:00:05.000Z", "cpu_usage": 20.0},
        {**GOOD, "timestamp": "2026-10-23T10:00:10.000Z", "cpu_usage": 30.0},
    ]
    r = c.post("/api/metrics", json=batch)
    assert r.status_code == 201
    assert r.get_json()["count"] == 3
    assert rows(path, "SELECT COUNT(*) FROM metrics") == [(3,)]


def test_offline_log_timestamp_preservation(env):
    c, path = env
    offline_ts = "2026-10-23T08:15:30.123Z"
    r = c.post("/api/metrics", json={**GOOD, "timestamp": offline_ts})
    assert r.status_code == 201
    assert rows(path, "SELECT timestamp FROM metrics") == [(offline_ts,)]


def test_agent_offline_local_logging_and_queue(tmp_path):
    import json
    import os
    from agent import append_offline_log, load_offline_queue, clear_offline_queue, log_metric_locally
    queue_file = str(tmp_path / "offline_queue.jsonl")
    log_file = str(tmp_path / "metrics.log")

    sample1 = {**GOOD, "timestamp": "2026-10-23T09:00:00.000Z"}
    sample2 = {**GOOD, "timestamp": "2026-10-23T09:00:05.000Z"}

    # 1. Test local metrics file logging
    log_metric_locally(log_file, sample1)
    log_metric_locally(log_file, sample2)
    with open(log_file) as f:
        lines = [json.loads(line) for line in f if line.strip()]
    assert len(lines) == 2
    assert lines[0]["timestamp"] == "2026-10-23T09:00:00.000Z"

    # 2. Test offline queue buffering
    append_offline_log(queue_file, sample1)
    append_offline_log(queue_file, sample2)
    queue = load_offline_queue(queue_file)
    assert len(queue) == 2
    assert queue[1]["timestamp"] == "2026-10-23T09:00:05.000Z"

    clear_offline_queue(queue_file)
    assert not os.path.exists(queue_file)


def test_offline_flush_with_server(tmp_path, monkeypatch):
    import os
    from agent import append_offline_log, flush_offline_queue
    path = str(tmp_path / "t.db")
    app = create_app(path)
    client = app.test_client()
    client.post("/api/register", json={"node_id": "NODE-01"})

    queue_file = str(tmp_path / "offline_queue.jsonl")
    payload1 = {**GOOD, "timestamp": "2026-10-23T09:00:00.000Z"}
    payload2 = {**GOOD, "timestamp": "2026-10-23T09:00:05.000Z"}
    append_offline_log(queue_file, payload1)
    append_offline_log(queue_file, payload2)

    class MockResponse:
        def __init__(self, status_code, json_data):
            self.status_code = status_code
            self._json = json_data

        def json(self):
            return self._json

    def mock_post(url, json=None, timeout=None, headers=None, **kwargs):
        endpoint = url.split("http://testserver")[-1]
        res = client.post(endpoint, json=json, headers=headers)
        return MockResponse(res.status_code, res.get_json())

    monkeypatch.setattr("agent.requests.post", mock_post)

    cfg = {"node_id": "NODE-01", "server": "http://testserver", "offline_queue": queue_file}
    flushed = flush_offline_queue(cfg)
    assert flushed == 2
    assert rows(path, "SELECT COUNT(*) FROM metrics") == [(2,)]
    assert not os.path.exists(queue_file)


def test_logs_export_csv_and_json(env):
    c, _ = env
    c.post("/api/thresholds", json={"metric_type": "cpu_usage", "limit_value": 30})
    c.post("/api/metrics", json={**GOOD, "cpu_usage": 75.0})

    # Test CSV export with breach marker on breached field only
    r_csv = c.get("/api/logs/export?format=csv")
    assert r_csv.status_code == 200
    assert r_csv.mimetype == "text/csv"
    csv_text = r_csv.get_data(as_text=True)
    assert "NODE-01" in csv_text
    assert "75.0 [ALERT]" in csv_text  # breached value marked

    # Test JSON export
    r_json = c.get("/api/logs/export?format=json")
    assert r_json.status_code == 200
    assert r_json.mimetype == "application/json"
    data = r_json.get_json()
    assert len(data) == 1
    assert data[0]["node_id"] == "NODE-01"

    # Test HTML export with threshold breach highlighting
    r_html = c.get("/api/logs/export?format=html")
    assert r_html.status_code == 200
    assert r_html.mimetype == "text/html"
    html_text = r_html.get_data(as_text=True)
    assert "NODE-01" in html_text
    assert "color:#cf222e" in html_text  # only breached value highlighted in red
    assert "75.0" in html_text

    # Test Excel (.xlsx) export with native red fill and font on breached cell only
    r_xlsx = c.get("/api/logs/export?format=xlsx")
    assert r_xlsx.status_code == 200
    assert r_xlsx.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    import io, openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(r_xlsx.data))
    ws = wb.active
    cpu_cell = ws.cell(row=2, column=3)  # CPU % (breached)
    mem_cell = ws.cell(row=2, column=4)  # Memory % (normal)
    assert cpu_cell.fill.start_color.rgb == "00FFEBE9" or cpu_cell.fill.start_color.value == "FFEBE9"
    assert cpu_cell.font.color.rgb == "00CF222E" or cpu_cell.font.color.value == "CF222E"
    assert mem_cell.fill.fill_type is None  # memory cell is normal


def test_logs_query_api(env):
    c, _ = env
    c.post("/api/metrics", json=GOOD)
    r = c.get("/api/logs")
    assert r.status_code == 200
    logs = r.get_json()["metrics"]
    assert len(logs) == 1
    assert logs[0]["node_id"] == "NODE-01"


def test_dashboard_offline_assets(env):
    c, _ = env
    r = c.get("/static/chart.umd.min.js")
    assert r.status_code == 200
    assert len(r.data) > 50000
    assert b"Chart" in r.data


def test_agent_load_config_and_node_isolation(tmp_path, monkeypatch):
    from agent import load_config
    cfg_file = tmp_path / "custom_node.json"
    cfg_file.write_text('{"node_id": "NODE-99", "server": "https://example.com", "interval": 10}')

    # 1. Load from file
    cfg1 = load_config(str(cfg_file))
    assert cfg1["node_id"] == "NODE-99"
    assert cfg1["server"] == "https://example.com"
    assert cfg1["interval"] == 10
    assert cfg1["log_file"] == "agent_NODE-99.log"
    assert cfg1["metrics_log"] == "metrics_NODE-99.log"
    assert cfg1["offline_queue"] == "offline_queue_NODE-99.jsonl"

    # 2. Load directly with node_id string
    cfg2 = load_config("NODE-CLI-02")
    assert cfg2["node_id"] == "NODE-CLI-02"
    assert cfg2["log_file"] == "agent_NODE-CLI-02.log"

    # 3. Environment variable override
    monkeypatch.setenv("NETMON_NODE_ID", "NODE-ENV")
    monkeypatch.setenv("NETMON_SERVER", "https://dnhbm.onrender.com")
    monkeypatch.setenv("NETMON_INTERVAL", "3")
    cfg3 = load_config()
    assert cfg3["node_id"] == "NODE-ENV"
    assert cfg3["server"] == "https://dnhbm.onrender.com"
    assert cfg3["interval"] == 3

