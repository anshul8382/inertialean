#!/usr/bin/env bash
# Diagnose cPanel Apache → Gunicorn routing for inertiainvest.in
set -euo pipefail

echo "=== Gunicorn backends ==="
for port in 5000 5004; do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "http://127.0.0.1:${port}/api/v1/health" 2>/dev/null || echo "000")
  echo "  :${port}/api/v1/health → HTTP ${code}"
done

echo ""
echo "=== Public HTTPS ==="
code=$(curl -sk -o /dev/null -w "%{http_code}" --max-time 10 "https://inertiainvest.in/api/v1/health" 2>/dev/null || echo "000")
echo "  https://inertiainvest.in/api/v1/health → HTTP ${code}"

echo ""
echo "=== userdata proxy files ==="
grep -rn '5000\|5004' /etc/apache2/conf.d/userdata 2>/dev/null | grep -i inertiainvest || echo "  (none)"

echo ""
echo "=== compiled httpd.conf (5000/5004 near inertiainvest) ==="
grep -n 'inertiainvest\|127.0.0.1:500' /etc/apache2/conf/httpd.conf 2>/dev/null | head -30 || true
