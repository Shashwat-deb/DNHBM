# Distributed Network Health & Bandwidth Monitor

Setup (once):  python3 -m venv .venv && .venv/bin/pip install flask psutil requests pytest

Run:
  .venv/bin/python server.py            # http://127.0.0.1:5000/dashboard
  .venv/bin/python agent.py config.json # one terminal per node; set node_id in each config

Tests (TC01-05, 07-09, 11-13 automated):  .venv/bin/python -m pytest -q

Notes
- Use 127.0.0.1, not "localhost": on macOS port 5000 is taken by AirPlay Receiver on IPv6.
- The dashboard loads Chart.js from a CDN, so it needs internet access.
- macOS: psutil.net_connections() needs root, so connection_count reads 0 unless the agent runs with sudo.
