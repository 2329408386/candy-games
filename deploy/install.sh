#!/bin/bash
# Runs on the server as root, from inside a release directory. Idempotent.
#   install.sh activate <release>   install that release and switch to it (rolls back if unhealthy)
#   install.sh rollback             switch to the release before the current one
#   install.sh status               show the running revision and the kept releases
#   install.sh cert                 first issue of the Let's Encrypt IP certificate (https); renewals run from a timer
# Layout: /opt/candy-games/releases/<time>-<sha>/  code, one per deploy (the newest $KEEP are kept)
#         /opt/candy-games/venvs/<hash>/           virtualenvs, shared by releases with the same requirements.txt
#         /opt/candy-games/current, venv           symlinks to the running release and its virtualenv
set -euo pipefail
APP=/opt/candy-games
DATA=/var/lib/candy-games
KEEP=5
CERT=/etc/letsencrypt/live/candy-games
IP=39.106.16.99

running() { basename "$(readlink -f $APP/current)"; }
releases() { ls -1 $APP/releases | sort; }

health() { # waits until every site answers with the expected revision on every worker
    local want=$1
    for _ in $(seq 1 30); do
        local ok=1
        for _ in 1 2 3 4 5 6; do
            for path in /health /math/health /english/health /teacher/health; do
                curl -fsS -m 3 "http://127.0.0.1:18082$path" 2>/dev/null | grep -q "\"revision\":\"$want\"" || ok=0
            done
        done
        [ $ok = 1 ] && return 0
        sleep 1
    done
    return 1
}

prepare() { # installs units, nginx config and the virtualenv for a release; prints the venv path
    local dir=$APP/releases/$1
    id candy-games >/dev/null 2>&1 || useradd --system --home $DATA --shell /usr/sbin/nologin candy-games
    install -d -o candy-games -g candy-games -m 700 $DATA
    test -f $DATA/secret.env || { echo "缺少 $DATA/secret.env（CHINESE_SECRET / MATH_SECRET / ENGLISH_SECRET）" >&2; exit 1; }
    local venv=$APP/venvs/$(sha256sum "$dir/requirements.txt" | cut -c1-12)
    if [ ! -f "$venv/.complete" ]; then
        # Built in place: a virtualenv cannot be renamed (its scripts hard-code their own path).
        rm -rf "$venv"
        python3 -m venv "$venv"
        "$venv/bin/pip" -q install -r "$dir/requirements.txt" >&2 || { rm -rf "$venv"; exit 1; }
        touch "$venv/.complete"
    fi
    chmod -R a+rX "$venv" "$dir"
    "$venv/bin/python" "$dir/tools/assets.py" --check >&2
    # Import the app with this venv before switching, so a broken release never replaces a working one.
    local scratch
    scratch=$(mktemp -d)
    (cd "$dir" && GAME_DATA=$scratch CHINESE_SECRET=check MATH_SECRET=check ENGLISH_SECRET=check "$venv/bin/python" -c "import wsgi; import core.essay.worker" >&2) ||
        { rm -rf "$scratch"; echo "新版本无法启动（import wsgi 失败）" >&2; exit 1; }
    rm -rf "$scratch"
    [ -x "$venv/bin/gunicorn" ] && "$venv/bin/gunicorn" --version >/dev/null
    echo "$venv"
}

install_config() { # returns 0 when the app unit changed and the service needs a restart instead of a reload
    local dir=$1 changed=1
    cmp -s "$dir/deploy/candy-games.service" /etc/systemd/system/candy-games.service && changed=0
    install -m 644 "$dir"/deploy/candy-games{,-backup,-watchdog}.service "$dir"/deploy/candy-games-{backup,watchdog}.timer /etc/systemd/system/
    # Releases from before the workbench have no worker unit: stop it when rolling back to one of them.
    if [ -f "$dir/deploy/candy-games-essay-worker.service" ]; then
        install -m 644 "$dir/deploy/candy-games-essay-worker.service" /etc/systemd/system/
    elif [ -f /etc/systemd/system/candy-games-essay-worker.service ]; then
        systemctl disable -q --now candy-games-essay-worker || true
        rm -f /etc/systemd/system/candy-games-essay-worker.service
    fi
    install -d /etc/systemd/system/nginx.service.d
    install -m 644 "$dir/deploy/nginx-restart.conf" /etc/systemd/system/nginx.service.d/restart.conf
    install -m 644 "$dir/deploy/nginx-http.conf" /etc/nginx/conf.d/candy-games.conf
    install -m 644 "$dir/deploy/nginx-static.conf" /etc/nginx/snippets/candy-games-static.conf
    install -m 644 "$dir/deploy/nginx-locations.conf" /etc/nginx/snippets/candy-games-locations.conf
    install -m 644 "$dir/deploy/nginx-site.conf" /etc/nginx/sites-available/candy-games
    ln -sfn /etc/nginx/sites-available/candy-games /etc/nginx/sites-enabled/candy-games
    install -d -m 755 /var/www/acme
    if [ -f $CERT/fullchain.pem ]; then
        install -m 644 "$dir/deploy/nginx-tls.conf" /etc/nginx/sites-available/candy-games-tls
        ln -sfn /etc/nginx/sites-available/candy-games-tls /etc/nginx/sites-enabled/candy-games-tls
    fi
    install -m 644 "$dir"/deploy/candy-games-cert.{service,timer} /etc/systemd/system/
    rm -f /etc/nginx/sites-enabled/default
    systemctl daemon-reload
    systemctl enable -q candy-games candy-games-backup.timer candy-games-watchdog.timer
    systemctl start candy-games-backup.timer candy-games-watchdog.timer
    if [ -x /opt/certbot/bin/certbot ]; then systemctl enable -q --now candy-games-cert.timer; fi
    nginx -t -q
    [ $changed = 1 ]
}

switch() { # points current and venv at a release, then reloads (or restarts when the unit or venv changed)
    local release=$1 venv=$2 restart=$3
    [ "$(readlink -f $APP/venv 2>/dev/null)" = "$(readlink -f "$venv")" ] || restart=1
    ln -sfn "$venv" $APP/venv.new && mv -T $APP/venv.new $APP/venv
    ln -sfn "releases/$release" $APP/current.new && mv -T $APP/current.new $APP/current
    if [ "$restart" = 1 ] || ! systemctl is-active -q candy-games; then systemctl restart candy-games; else systemctl reload candy-games; fi
    systemctl reload nginx
    # The essay worker runs the release's code: restart it on every switch (it has no graceful reload).
    if [ -f /etc/systemd/system/candy-games-essay-worker.service ]; then
        systemctl enable -q candy-games-essay-worker
        systemctl restart candy-games-essay-worker
    fi
}

activate() {
    local release=$1 previous restart=0 venv
    previous=$( [ -e $APP/current ] && running || true)
    venv=$(prepare "$release")
    # Back up before switching; the backup timer keeps doing this daily.
    systemctl start candy-games-backup.service && echo "已备份数据库"
    install_config "$APP/releases/$release" && restart=1
    switch "$release" "$venv" $restart
    if health "$(cat $APP/releases/$release/REVISION)"; then
        echo "发布完成：$release"
        # 语文老师工作台: the first release with it creates its admin account (credentials in a private file).
        if [ -f "$APP/current/sites/teacher/prompts/grade.md" ]; then
            (set -a; . $DATA/secret.env; cd $APP/current && runuser -u candy-games -- env GAME_DATA=$DATA "$APP/venv/bin/python" manage.py seed --site teacher | grep -v "^初始化完成") || echo "工作台初始化失败（不影响已发布的版本）" >&2
        fi
    else
        echo "健康检查失败" >&2
        if [ -n "$previous" ] && [ "$previous" != "$release" ]; then
            install_config "$APP/releases/$previous" || true
            switch "$previous" "$(prepare "$previous")" 1
            echo "已回滚到 $previous" >&2
        fi
        exit 1
    fi
    releases | head -n -$KEEP | grep -vx -e "$release" -e "$previous" | while read -r old; do rm -rf "${APP:?}/releases/$old"; done
    for v in $APP/venvs/*; do # drop virtualenvs no kept release uses
        [ "$v" = "$(readlink -f $APP/venv)" ] && continue
        used=0
        for r in $APP/releases/*; do [ "$v" = "$APP/venvs/$(sha256sum "$r/requirements.txt" | cut -c1-12)" ] && used=1; done
        [ $used = 1 ] || rm -rf "$v"
    done
}

case "${1:-}" in
activate) activate "$2" ;;
rollback)
    current=$(running)
    previous=$(releases | grep -B1 -x "$current" | head -n 1)
    [ -n "$previous" ] && [ "$previous" != "$current" ] || { echo "没有更早的版本可回滚" >&2; exit 1; }
    restart=0
    install_config "$APP/releases/$previous" && restart=1
    switch "$previous" "$(prepare "$previous")" $restart
    health "$(cat $APP/releases/$previous/REVISION)" && echo "已回滚到 $previous"
    ;;
cert)
    # certbot lives in its own virtualenv: python3 -m venv /opt/certbot && /opt/certbot/bin/pip install certbot
    [ -x /opt/certbot/bin/certbot ] || { echo "请先安装 certbot 到 /opt/certbot" >&2; exit 1; }
    install -d -m 755 /var/www/acme
    /opt/certbot/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
        --cert-name candy-games --webroot -w /var/www/acme --ip-address $IP --preferred-profile shortlived
    install_config "$APP/current" || true
    systemctl reload nginx
    echo "https 已启用：https://$IP/"
    ;;
status)
    echo "运行中：$(running)（提交 $(cat $APP/current/REVISION)）"
    releases | sed "s/^/  /"
    ;;
*)
    echo "用法: install.sh activate <release> | rollback | status" >&2
    exit 2
    ;;
esac
