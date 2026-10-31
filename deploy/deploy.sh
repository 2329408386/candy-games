#!/bin/bash
# Local: test, sync to the server and reload without dropping requests.  Usage: deploy/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER=${SERVER:-root@39.106.16.99}
python3 tools/assets.py --check
python3 -m pytest -q tests
rsync -az --delete --exclude venv --exclude .git --exclude __pycache__ --exclude .pytest_cache ./ "$SERVER":/opt/candy-games/
ssh "$SERVER" 'set -e; /opt/candy-games/deploy/install.sh && systemctl reload candy-games && systemctl reload nginx
for i in $(seq 1 20); do curl -fsS -m 3 -o /dev/null http://127.0.0.1/health && curl -fsS -m 3 -o /dev/null http://127.0.0.1/math/health && echo 发布完成 && exit 0; sleep 1; done
echo 健康检查失败; exit 1'
