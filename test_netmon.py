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
