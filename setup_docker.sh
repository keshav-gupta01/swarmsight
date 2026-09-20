#!/usr/bin/env bash
# =============================================================================
# SwarmSight EC2 Step 1 Bootstrap Script
# Run this on the EC2 instance to containerize the deployment.
#
# Usage:
#   sudo bash setup_docker.sh
# =============================================================================

set -euo pipefail
LOG="/var/log/swarmsight_setup.log"
exec > >(tee -a "$LOG") 2>&1

echo "[$(date)] ====== SwarmSight Step 1: Containerize ======"

APP_DIR="/opt/swarmsight"
CLIPS_DIR="$APP_DIR/data/clips"
IMAGE_NAME="swarmsight"
IMAGE_TAG="latest"

# ── 1. Install Docker ────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "[+] Installing Docker..."
    apt-get update -qq
    apt-get install -y --no-install-recommends docker.io
    systemctl enable docker
    systemctl start docker
    usermod -aG docker ubuntu
    echo "[+] Docker installed: $(docker --version)"
else
    echo "[=] Docker already installed: $(docker --version)"
fi

# ── 2. Install Nginx ────────────────────────────────────────────────────────
if ! command -v nginx &>/dev/null; then
    echo "[+] Installing Nginx..."
    apt-get install -y --no-install-recommends nginx
    echo "[+] Nginx installed: $(nginx -v 2>&1)"
else
    echo "[=] Nginx already installed: $(nginx -v 2>&1)"
fi

# ── 3. Install Docker Compose ────────────────────────────────────────────────
if ! command -v docker-compose &>/dev/null && ! docker compose version &>/dev/null 2>&1; then
    echo "[+] Installing Docker Compose plugin..."
    apt-get install -y --no-install-recommends docker-compose-plugin
fi

# ── 4. Stage app files ───────────────────────────────────────────────────────
echo "[+] Staging app files into $APP_DIR/src ..."
mkdir -p "$APP_DIR/src"
cd "$APP_DIR/src"

# Files are expected to be in this directory already (via scp or tarball)
# Check they exist
for f in Dockerfile requirements.txt docker-compose.yml nginx.conf; do
    if [[ ! -f "$f" ]]; then
        echo "[ERROR] Missing file: $f — copy files first!" >&2
        exit 1
    fi
done

# ── 5. Build Docker image ────────────────────────────────────────────────────
echo "[+] Building Docker image $IMAGE_NAME:$IMAGE_TAG ..."
docker build -t "$IMAGE_NAME:$IMAGE_TAG" .
echo "[+] Image built."

# ── 6. Start new container on port 8001 (spare port for swap) ───────────────
echo "[+] Starting new container on port 8001 ..."
docker rm -f swarmsight-new 2>/dev/null || true
docker run -d --name swarmsight-new \
    -p 127.0.0.1:8001:8000 \
    -v "$CLIPS_DIR:/opt/swarmsight/data/clips:ro" \
    -e AWS_REGION=ap-south-1 \
    -e SWARMSIGHT_TABLE=swarmsight_alerts \
    -e "SWARMSIGHT_SNS_ARN=arn:aws:sns:ap-south-1:879268673611:swarmsight-alerts" \
    --restart always \
    "$IMAGE_NAME:$IMAGE_TAG"

echo "[+] Waiting 5s for container to start..."
sleep 5

# ── 7. Health check new container ───────────────────────────────────────────
echo "[+] Health-checking port 8001..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/ 2>/dev/null || echo "000")
if [[ "$HTTP_CODE" != "200" ]]; then
    echo "[ERROR] Health check failed! Got HTTP $HTTP_CODE. Aborting." >&2
    docker logs swarmsight-new --tail 30
    exit 1
fi
echo "[+] Health check passed (HTTP $HTTP_CODE)."

# ── 8. Install Nginx config ──────────────────────────────────────────────────
echo "[+] Installing Nginx config..."
cp nginx.conf /etc/nginx/sites-available/swarmsight
ln -sf /etc/nginx/sites-available/swarmsight /etc/nginx/sites-enabled/swarmsight
rm -f /etc/nginx/sites-enabled/default

nginx -t
nginx -s reload || systemctl restart nginx
echo "[+] Nginx reloaded."

# ── 9. Final public link check ───────────────────────────────────────────────
echo "[+] Checking public port 8000 via Nginx..."
sleep 2
HTTP_CODE_PUB=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ 2>/dev/null || echo "000")
if [[ "$HTTP_CODE_PUB" != "200" ]]; then
    echo "[WARN] Public port 8000 returned HTTP $HTTP_CODE_PUB — check Nginx logs."
else
    echo "[+] Public port 8000 OK (HTTP $HTTP_CODE_PUB)."
fi

# ── 10. Stop old uvicorn systemd service ─────────────────────────────────────
echo "[+] Stopping old swarmsight systemd unit..."
systemctl stop swarmsight 2>/dev/null || true
systemctl disable swarmsight 2>/dev/null || true

# ── 11. Install new Docker-managed systemd unit ──────────────────────────────
cat > /etc/systemd/system/swarmsight.service <<'UNIT'
[Unit]
Description=SwarmSight Crowd Safety Platform (Docker)
After=docker.service
Requires=docker.service

[Service]
Type=simple
Restart=always
RestartSec=5
ExecStart=/usr/bin/docker start -a swarmsight-new
ExecStop=/usr/bin/docker stop swarmsight-new

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable swarmsight
systemctl restart swarmsight

echo "[$(date)] ====== Step 1 Complete ======"
echo "Container: $(docker ps --filter name=swarmsight-new --format '{{.Status}}')"
echo "Nginx: $(systemctl is-active nginx)"
echo "Public link: http://$(curl -s http://169.254.169.254/latest/meta-data/public-ipv4 -H 'X-aws-ec2-metadata-token-ttl-seconds: 60'):8000"
