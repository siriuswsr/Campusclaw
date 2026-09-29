#!/bin/bash
set -e

MYSQL_HOST="${MYSQL_HOST:-mysql}"
MYSQL_PORT="${MYSQL_PORT:-3306}"
QDRANT_URL="${QDRANT_URL:-http://qdrant:6333}"

echo "[entrypoint] Waiting for MySQL at $MYSQL_HOST:$MYSQL_PORT ..."
for i in $(seq 1 60); do
  if python -c "import pymysql, os; pymysql.connect(host='$MYSQL_HOST', port=$MYSQL_PORT, user=os.environ.get('MYSQL_USER','campus'), password=os.environ.get('MYSQL_PASSWORD','campus'), database=os.environ.get('MYSQL_DATABASE','campusclaw'), connect_timeout=2)" 2>/dev/null; then
    echo "[entrypoint] MySQL ready."
    break
  fi
  sleep 1
done

echo "[entrypoint] Waiting for Qdrant at $QDRANT_URL ..."
for i in $(seq 1 60); do
  if python -c "import urllib.request; urllib.request.urlopen('$QDRANT_URL/collections', timeout=2)" 2>/dev/null; then
    echo "[entrypoint] Qdrant ready."
    break
  fi
  sleep 1
done

echo "[entrypoint] Running init_db.py ..."
python scripts/init_db.py

echo "[entrypoint] Starting gunicorn on 0.0.0.0:8080"
exec gunicorn -w 1 -b 0.0.0.0:8080 "app:create_app()"
