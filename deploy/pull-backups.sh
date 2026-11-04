#!/bin/bash
# Run on the dev machine: copies the server's newest backup (database + secret.env) to a local folder, so a lost server disk
# does not lose the licenses. Installed in the dev machine's crontab (crontab -e), daily after the server's 03:15 backup:
#   15 4 * * * /path/to/candy-games/deploy/pull-backups.sh >/dev/null
# Keeps the last KEEP_DAYS days here, like the server.
set -euo pipefail
SERVER=${SERVER:-root@39.106.16.99}
DEST=${DEST:-$HOME/candy-games-backups}
KEEP_DAYS=${KEEP_DAYS:-3}
install -d -m 700 "$DEST"
latest=$(ssh "$SERVER" 'ls -1 /var/lib/candy-games/backups | tail -n 1')
[ -n "$latest" ] || { echo "服务器上没有备份" >&2; exit 1; }
install -d -m 700 "$DEST/$latest"
rsync -a "$SERVER":/var/lib/candy-games/backups/"$latest"/{games.sqlite3,secret.env} "$DEST/$latest"/
find "$DEST" -mindepth 1 -maxdepth 1 -type d -mmin +$((KEEP_DAYS * 1440)) -exec rm -rf {} +
echo "已同步到 $DEST/$latest"
