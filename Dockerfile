FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py agent.py config.json ./
COPY static ./static

# Hosts (Render, Railway, Fly.io) set PORT. DB_PATH should point to a persistent volume.
ENV PORT=5000 DB_PATH=/data/monitor.db
RUN mkdir -p /data

EXPOSE 5000

# One worker because SQLite is a single file; threads handle concurrent agents.
CMD ["sh", "-c", "gunicorn -w 1 --threads 8 -b 0.0.0.0:${PORT} 'server:create_app()'"]
