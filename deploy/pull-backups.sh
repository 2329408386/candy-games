#!/bin/bash
# Run on the dev machine: copies the server's daily backups (database + secret.env) to a local folder, so a lost
# server disk does not lose the licenses. Not installed anywhere automatically; to run it daily add e.g.
#   15 4 * * * /path/to/candy-games/deploy/pull-backups.sh >/dev/null
# to the dev machine's crontab (crontab -e).
set -euo pipefail
SERVER=${SERVER:-root@39.106.16.99}
DEST=${DEST:-$HOME/candy-games-backups}
install -d -m 700 "$DEST"
# The server keeps 3 days; keep everything here (no --delete) and let the owner prune.
rsync -a "$SERVER":/var/lib/candy-games/backups/ "$DEST"/
echo "已同步到 $DEST：$(ls -1 "$DEST" | tail -n 1)"
