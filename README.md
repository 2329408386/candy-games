# candy-games：语文 / 数学小游戏

一个服务同时提供两个站点，网址与合并前完全一致：

| 站点 | 网址 | 后台 |
|---|---|---|
| 语文 | `http://39.106.16.99/` | `/admin` |
| 数学 | `http://39.106.16.99/math/` | `/math/admin` |

## 结构
- `wsgi.py`：一个 gunicorn 进程里挂两个 Flask 站点（语文在 `/`，数学在 `/math`）。
- `core/app.py`：共享的路由、授权、后台逻辑；`core/schema.py`：数据库表结构。
- `sites/<chinese|math>/`：站点配置 `sites/__init__.py`、游戏页 `games/`、静态资源 `static/`、站点专属模板（首页、外框）。
- `core/templates/`：两站共用的后台模板。
- `shared-static/game-audio-v3/`：两站共用的音效。
- `tools/assets.py`：静态资源指纹；`tools/merge_databases.py`：合并旧库（仅迁移时用过一次）。
- `deploy/`：systemd、nginx 配置和发布脚本。

两站各自保留独立的密钥（`CHINESE_SECRET`、`MATH_SECRET`）、Cookie 名（`session`、`math_session`）、管理员账号和授权编号。所有数据存在同一个数据库 `/var/lib/candy-games/games.sqlite3` 里，每张表都按 `site` 列区分站点。

## 修改静态资源后
图片、音效文件名里带内容哈希（`name.<8位哈希>.ext`），js/css 的引用带 `?v=<哈希>`，浏览器会缓存一年，内容一变地址就变，客户端自动拿到新版本。替换或修改任何静态文件后运行：

```bash
python3 tools/assets.py --fix     # 自动改名并更新所有引用
python3 tools/assets.py --check   # 测试和发布时会自动检查
```

## 测试
```bash
python3 -m venv venv && venv/bin/pip install -r requirements.txt pytest
venv/bin/python -m pytest -q tests
```

## 发布
```bash
deploy/deploy.sh      # 跑测试 → rsync 到 /opt/candy-games → 平滑重载（不中断请求）→ 健康检查
```

## 服务器运维
- 服务：`systemctl status candy-games`；日志：`journalctl -u candy-games`。
- 自动恢复：进程退出后 1 秒内由 systemd 重新拉起（不限次数）；`candy-games-watchdog.timer` 每 30 秒检查两个站点的 `/health`，卡死时自动重启服务；nginx 也设置了自动重启。
- 备份：`candy-games-backup.timer` 每天 03:15 备份到 `/var/lib/candy-games/backups/`，保留 30 天。
- 管理命令（在服务器上）：
  ```bash
  cd /opt/candy-games
  sudo -u candy-games env $(cat /var/lib/candy-games/secret.env) GAME_DATA=/var/lib/candy-games venv/bin/python manage.py summary
  # export [--site chinese|math] / backup / reset-password --site chinese|math
  ```
