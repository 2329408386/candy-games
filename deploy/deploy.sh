#!/bin/bash
# Run on the dev machine. Ships the committed HEAD to the server as a new release and switches to it.
#   deploy/deploy.sh            test, upload, switch, health-check (rolls back by itself if unhealthy)
#   deploy/deploy.sh rollback   switch back to the previous release
#   deploy/deploy.sh status     show the running revision and the kept releases
# FORCE=1 skips the "clean and pushed" check, SKIP_TESTS=1 skips the local tests.
set -euo pipefail
cd "$(dirname "$0")/.."
SERVER=${SERVER:-root@39.106.16.99}
APP=/opt/candy-games

case "${1:-deploy}" in
status | rollback)
    ssh "$SERVER" "$APP/current/deploy/install.sh $1"
    exit
    ;;
deploy) ;;
*)
    echo "用法: deploy/deploy.sh [deploy|rollback|status]" >&2
    exit 2
    ;;
esac

if [ "${FORCE:-}" != 1 ]; then
    [ -z "$(git status --porcelain)" ] || { echo "工作区有未提交的改动，请先提交（或 FORCE=1）" >&2; exit 1; }
    git fetch -q origin
    [ "$(git rev-parse HEAD)" = "$(git rev-parse '@{upstream}')" ] || { echo "HEAD 与远程分支不一致，请先 git push（或 FORCE=1）" >&2; exit 1; }
fi
if [ "${SKIP_TESTS:-}" != 1 ]; then
    PY=python3; [ -x venv/bin/python ] && PY=venv/bin/python
    $PY tools/assets.py --check
    $PY tools/banks.py --check
    $PY -m pytest -q tests
fi

SHA=$(git rev-parse --short HEAD)
RELEASE=$(date +%Y%m%d-%H%M%S)-$SHA
echo "上传 $RELEASE"
# export-ignore in .gitattributes keeps docs and tests out of the archive.
git archive --format=tar HEAD | ssh "$SERVER" "set -e; mkdir -p $APP/releases/$RELEASE; tar -x -C $APP/releases/$RELEASE; echo $SHA > $APP/releases/$RELEASE/REVISION"
ssh "$SERVER" "$APP/releases/$RELEASE/deploy/install.sh activate $RELEASE"
