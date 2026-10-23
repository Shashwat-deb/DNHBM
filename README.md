# Distributed Network Health & Bandwidth Monitor

Setup (once):  python3 -m venv .venv && .venv/bin/pip install flask psutil requests pytest

Run:
  .venv/bin/python server.py            # http://127.0.0.1:5000/dashboard
  .venv/bin/python agent.py config.json # one terminal per node; set node_id in each config

Tests (TC01-05, 07-09, 11-13 automated):  .venv/bin/python -m pytest -q

Deploy (public server)
- Build and run with the included Dockerfile on any host that runs containers (Render, Railway, Fly.io, a VPS).
- Set environment variables on the host:
  - `DNHBM_API_KEY`: required for public use. All POST requests must send it as `X-API-Key`.
  - `DB_PATH`: path to SQLite on a persistent volume, for example `/data/monitor.db`. Without a volume, data is lost on restart.
  - `PORT`: set by most hosts automatically.
- Agents on each PC: set `"server": "https://<your-host>"` in `config.json`, and set the key with `DNHBM_API_KEY` (or the `api_key` field). Run `python agent.py config.json` as before.
- Dashboard: `https://<your-host>/dashboard`. Changing a threshold or clearing alerts asks for the key once per browser tab.

Notes
- Use 127.0.0.1, not "localhost": on macOS port 5000 is taken by AirPlay Receiver on IPv6.
- The dashboard loads Chart.js from a CDN, so it needs internet access.
- macOS: psutil.net_connections() needs root, so connection_count reads 0 unless the agent runs with sudo.
