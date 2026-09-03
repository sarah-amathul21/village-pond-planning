#!/usr/bin/env bash
set -e

SSH_PORT=2229
SERVER_IP="10.1.75.51"
USER="student"
HTTP_PORT=3229

echo "=========================================================="
echo " Deploying Village Pond Planning Backend to $SERVER_IP:$SSH_PORT"
echo "=========================================================="

echo "[1/3] Copying project files to $USER@$SERVER_IP..."
# Exclude heavy local venv and .git; stream via tar over ssh (does not require rsync)
tar --exclude='venv' --exclude='.git' -czf - -C /home/amathul/Downloads pond_planning_phase2 | ssh -p $SSH_PORT $USER@$SERVER_IP "tar -xzf - -C ~/"

echo "[2/3] Starting backend on remote server (port $HTTP_PORT)..."
ssh -p $SSH_PORT $USER@$SERVER_IP "bash -l -c 'cd ~/pond_planning_phase2 && chmod +x run_backend.sh && ./run_backend.sh'"

echo "[3/3] Testing endpoint from laptop..."
sleep 2
echo "GET test:"
curl -s "http://$SERVER_IP:$HTTP_PORT/" || echo "Warning: Connection pending"
echo ""
echo "POST /findCatchment test with sample file:"
curl -s -X POST "http://$SERVER_IP:$HTTP_PORT/findCatchment" \
     -F "contour_map=@/home/amathul/Downloads/pond_planning_phase2/contours_1m.kml" | head -c 250
echo -e "\n..."

echo ""
echo "=========================================================="
echo " SUCCESS! Your backend URL for submission is:"
echo " http://$SERVER_IP:$HTTP_PORT/findCatchment"
echo "=========================================================="
