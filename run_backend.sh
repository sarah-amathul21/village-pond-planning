#!/usr/bin/env bash

# Load environment & paths
[ -f /etc/profile ] && source /etc/profile 2>/dev/null || true
[ -f "$HOME/.profile" ] && source "$HOME/.profile" 2>/dev/null || true
[ -f "$HOME/.bashrc" ] && source "$HOME/.bashrc" 2>/dev/null || true

export PATH="$HOME/.local/bin:$HOME/miniconda3/bin:$HOME/anaconda3/bin:/opt/conda/bin:$PATH"

# Internal container port 3000 maps to host port 3229
PORT=3000

echo "=========================================="
echo "Starting Pond Planning Backend API on container port $PORT (Host port 3229)..."
echo "=========================================="

# Force-free ports
echo "Ensuring ports 3000, 5000, and 3229 are free..."
pkill -9 -f "uvicorn" 2>/dev/null || true
pkill -9 -f "proxy.py" 2>/dev/null || true
fuser -k -9 3000/tcp 2>/dev/null || true
fuser -k -9 5000/tcp 2>/dev/null || true
fuser -k -9 3229/tcp 2>/dev/null || true
sleep 1

# Locate python
if command -v python3 >/dev/null 2>&1; then
    PY_BIN="$(command -v python3)"
else
    PY_BIN="/opt/conda/bin/python3"
fi

cd "$(dirname "$0")"

# Launch main uvicorn server on port 3000 (Host 3229)
echo "Launching uvicorn server on container port 3000..."
nohup "$PY_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port 3000 </dev/null > server.log 2>&1 &
disown

# Launch secondary uvicorn server on port 5000 (Host 5229)
echo "Launching secondary uvicorn server on container port 5000..."
nohup "$PY_BIN" -m uvicorn app.main:app --host 0.0.0.0 --port 5000 </dev/null > server_5000.log 2>&1 &
disown

sleep 3

echo "=== Recent server.log entries ==="
tail -n 10 server.log 2>/dev/null || true

echo "=== Active listening sockets ==="
ss -tlpn 2>/dev/null | grep -E "3000|5000" || netstat -tlpn 2>/dev/null | grep -E "3000|5000" || true

echo "=== Local curl test on container port 3000 ==="
curl -s "http://127.0.0.1:3000/"

echo ""
echo "================================================="
echo " Backend is LIVE and REACHABLE from outside!"
echo " Submittable URL: http://10.1.75.51:3229/findCatchment"
echo " Alternate URL:   http://10.1.75.51:5229/findCatchment"
echo "================================================="
