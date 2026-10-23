# Distributed Network Health & Bandwidth Monitor

A lightweight, robust distributed network and system telemetry monitoring platform. Collects CPU, memory, bandwidth throughput, packet loss, and connection count from distributed nodes, with real-time browser dashboard visualization, threshold alerting, store-and-forward offline buffering, and multi-format log exports (Excel, CSV, HTML, JSON) with field-level threshold breach highlighting.

---

## 🌐 Live Cloud Deployment (Render)

The central monitoring server and dashboard are deployed live on Render:
- **Dashboard URL:** [https://dnhbm.onrender.com/](https://dnhbm.onrender.com/) (or [https://dnhbm.onrender.com/dashboard](https://dnhbm.onrender.com/dashboard))
- **Server API Base:** `https://dnhbm.onrender.com`

Any node running anywhere on the internet can report telemetry directly to this deployed central server.

---

## 🚀 Quickstart & Setup

### 1. Prerequisites & Installation
```bash
# Clone the repository
git clone https://github.com/Shashwat-deb/DNHBM.git netmon
cd netmon

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Run the Default Agent (Reports to Render Live Server)
By default, `agent.py` connects to the cloud deployment on Render:
```bash
python agent.py
```
Open [https://dnhbm.onrender.com/](https://dnhbm.onrender.com/) to see your node reporting live telemetry!

---

## 🖥️ How to Add / Create New Nodes

You can create and run as many nodes as you need. Each node needs a unique `node_id`. You can configure nodes via **JSON config files**, **CLI arguments**, or **environment variables**.

### Method 1: Using JSON Configuration Files (Recommended)

1. Create a new JSON file for the node, e.g. `node2.json` (a ready-to-use template is available in `node2_example.json`):
```json
{
  "node_id": "NODE-02",
  "server": "https://dnhbm.onrender.com",
  "interval": 5,
  "api_key": "",
  "log_file": "agent_NODE-02.log",
  "metrics_log": "metrics_NODE-02.log",
  "offline_queue": "offline_queue_NODE-02.jsonl"
}
```

2. Run the agent with your config file:
```bash
python agent.py node2.json
```

3. To add another node (`NODE-03`):
```bash
cp node2_example.json node3.json
# Edit "node_id": "NODE-03" in node3.json
python agent.py node3.json
```

---

### Method 2: Direct CLI Arguments (Fastest)

Pass the node name and optional server URL directly on the command line:

```bash
# Syntax: python agent.py <NODE_ID> [SERVER_URL]

# Connect node to Render live server:
python agent.py NODE-02

# Connect node to a custom or local server:
python agent.py NODE-02 http://127.0.0.1:5000
```

---

### Method 3: Environment Variables (Ideal for Docker / VMs / Cloud)

For containers, systemd services, or CI/CD pipelines, configure nodes using environment variables:

```bash
export NETMON_NODE_ID="WORKER-NODE-01"
export NETMON_SERVER="https://dnhbm.onrender.com"
export NETMON_INTERVAL="5"

python agent.py
```

Or as a one-liner:
```bash
NETMON_NODE_ID=SERVER-EAST-01 NETMON_SERVER=https://dnhbm.onrender.com python agent.py
```

---

### Running Multiple Nodes on the Same Machine

When running multiple agents on the same workstation:
- Open a separate terminal window for each node.
- Each node automatically uses isolated log files (`agent_<node_id>.log`, `metrics_<node_id>.log`, `offline_queue_<node_id>.jsonl`), preventing file contention and queue conflicts.

---

## 💻 Running the Server Locally

If you prefer to run both the central server and agents locally:

1. **Start the Local Server:**
```bash
python server.py
```
> **macOS Note:** On macOS, AirPlay Receiver listens on port 5000. `server.py` automatically binds to both port `5000` and port `5001`. If `http://127.0.0.1:5000/dashboard` shows 403 Forbidden, use `http://127.0.0.1:5001/dashboard`.

2. **Run Local Agents:**
Use the provided `config.local.json`:
```bash
python agent.py config.local.json
```
or via CLI:
```bash
python agent.py NODE-LOCAL http://127.0.0.1:5000
```

---

## 📦 Architecture & Key Features

### 1. Offline Availability & Telemetry Buffering
- **Local Metrics Logging:** Agents persistently log every sample locally to `metrics.log` (or `metrics_<node_id>.log`) as JSON lines.
- **Store-and-Forward Queue:** If network connectivity drops or the server goes down:
  1. The agent queues unsent metrics into `offline_queue.jsonl`.
  2. Preserves exact client measurement timestamps.
  3. Once connectivity is restored, the agent automatically flushes the queue via batch ingestion without data loss.
- **Self-Contained Offline Dashboard:** Chart.js is bundled locally (`static/chart.umd.min.js`), allowing dashboard viewing even in air-gapped environments.

### 2. Dynamic Threshold Configuration & Alerts
- Set thresholds directly from the dashboard top-right panel for:
  - CPU Usage (%)
  - Memory Usage (%)
  - Network Throughput (Mbps)
  - Packet Loss (%)
  - Connection Count
- Breaches immediately trigger alerts in the dedicated scrollable notification box.
- Active alerts from the last 30 minutes are shown, and the full alert history can be downloaded via `/api/alerts/export` (CSV).

### 3. Log Export with Field-Level Breach Highlighting
Download telemetry logs in four formats:
- **Excel (`.xlsx` / `.xls`):** Only the specific metric value exceeding the threshold is highlighted with a soft red fill (`#FFEBE9`) and bold red text (`#CF222E`), leaving normal cells unstyled.
- **CSV (`.csv`):** Breached fields are marked inline with an `[ALERT]` tag (e.g., `85.4 [ALERT]`).
- **HTML (`.html`):** Clean, styled report with red highlighted alert badges.
- **JSON (`.json`):** Raw structured telemetry records.

---

## 🔐 Security & Optional API Key

For production or public deployments:
- Set `DNHBM_API_KEY` in server environment variables. When set, all incoming `POST` requests require the matching `X-API-Key` header.
- The web dashboard will prompt for the API key once per browser session.
- Agents can send the key via `config.json` (`"api_key": "..."`) or via `DNHBM_API_KEY` environment variable.

---

## 🧪 Testing

Run the full automated test suite:

```bash
.venv/bin/python -m pytest -v
```

---

## 📋 REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/register` | `POST` | Register or update node metadata (`node_id`, `hostname`, `os`, `interval`) |
| `/api/metrics` | `POST` | Ingest single metric sample or batch array of buffered offline samples |
| `/api/thresholds` | `GET`, `POST` | Retrieve or set metric alert thresholds |
| `/api/alerts` | `GET` | List active threshold breach alerts (supports `?minutes=N`) |
| `/api/alerts/export` | `GET` | Download full alert history as CSV |
| `/api/alerts/clear` | `POST` | Deactivate active alerts |
| `/api/nodes` | `GET` | Retrieve status of all registered nodes (ONLINE/OFFLINE) |
| `/api/history` | `GET` | Query historical metrics with optional `node_id`, `start`, `end` filters |
| `/api/logs` | `GET` | Query paginated telemetry logs with current threshold context |
| `/api/logs/export` | `GET` | Export logs (`?format=xlsx`, `csv`, `html`, `json`) with breach highlighting |
| `/dashboard` | `GET` | Serve real-time telemetry dashboard |

---

## ☁️ Deployment (Render)

This repository includes `render.yaml` pre-configured for one-click deployment on Render:
- **Runtime:** Python 3
- **Web Server:** Gunicorn (`gunicorn -w 1 --threads 8 -b 0.0.0.0:$PORT "server:create_app()"`)
- **Live Deployment:** [https://dnhbm.onrender.com/](https://dnhbm.onrender.com/)
