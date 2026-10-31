#!/bin/bash
# Run on the server as root from /opt/candy-games after the code is synced. Idempotent.
set -euo pipefail
cd /opt/candy-games
id candy-games >/dev/null 2>&1 || useradd --system --home /var/lib/candy-games --shell /usr/sbin/nologin candy-games
install -d -o candy-games -g candy-games -m 700 /var/lib/candy-games
test -f /var/lib/candy-games/secret.env || { echo "缺少 /var/lib/candy-games/secret.env（CHINESE_SECRET / MATH_SECRET）"; exit 1; }
chmod -R a+rX /opt/candy-games

[ -x venv/bin/python ] || python3 -m venv venv
venv/bin/pip -q install -r requirements.txt
venv/bin/python tools/assets.py --check

install -m 644 deploy/candy-games.service deploy/candy-games-backup.service deploy/candy-games-backup.timer \
    deploy/candy-games-watchdog.service deploy/candy-games-watchdog.timer /etc/systemd/system/
install -d /etc/systemd/system/nginx.service.d
install -m 644 deploy/nginx-restart.conf /etc/systemd/system/nginx.service.d/restart.conf
install -m 644 deploy/nginx-http.conf /etc/nginx/conf.d/candy-games.conf
install -m 644 deploy/nginx-static.conf /etc/nginx/snippets/candy-games-static.conf
install -m 644 deploy/nginx-site.conf /etc/nginx/sites-available/candy-games
systemctl daemon-reload
nginx -t
