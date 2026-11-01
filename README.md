# candy-games：语文 / 数学小游戏

一个服务同时提供两个站点，网址与合并前完全一致：

| 站点 | 网址 | 后台 |
|---|---|---|
| 语文 | `http://39.106.16.99/` | `/admin` |
| 数学 | `http://39.106.16.99/math/` | `/math/admin` |

## 结构
- `wsgi.py`：一个 gunicorn 进程里挂两个 Flask 站点（语文在 `/`，数学在 `/math`）。
- `core/app.py`：共享的路由、授权、后台逻辑；`core/schema.py`：数据库表结构。
- `sites/<chinese|math>/`：站点配置 `sites/__init__.py`、题库 `banks/<游戏>.json`、游戏页外壳 `games/`、静态资源 `static/`、站点专属模板（首页、外框）。
- `core/templates/`：两站共用的后台模板。
- `shared-static/pk/`：两站共用的游戏引擎 `pk-core.js`/`pk-core.css` 和各题型渲染器 `types/`；`shared-static/game-audio-v3/`：共用音效。
- `tools/assets.py`：静态资源指纹；`tools/banks.py`：题库校验并生成复核清单。
- `docs/`：由题库生成的复核清单（不要手改）。
- `deploy/`：systemd、nginx 配置和发布脚本。

两站各自保留独立的密钥（`CHINESE_SECRET`、`MATH_SECRET`）、Cookie 名（`session`、`math_session`）、管理员账号和授权编号。所有数据存在同一个数据库 `/var/lib/candy-games/games.sqlite3` 里，每张表都按 `site` 列区分站点。

## 游戏规则
- 每款游戏题库 100 题，每局每人抽 20 题（按题型均衡抽取），同局双方不重复。可选双人PK、单人练习、错题重练。
- 题库只能在授权后通过 `/games/<游戏>/bank.json` 获取。
- 免费试玩：未授权的浏览器每个游戏可玩一局（点开始即用掉，记录在 `trials` 表，按会话里的设备令牌区分），试玩只下发够一局的题；结束后弹出管理员微信二维码（`shared-static/contact/`）。首页右上角「获取授权码」也会打开这个二维码。
- 游戏页右上角「投屏」：整体放大字号并全屏，适合教室投影；按 Esc 退出全屏时同时退出投屏。
- 答错的题记入本机错题本（localStorage `chinese-mistakes-v1` / `math-mistakes-v1`，最多 300 条），首页右上角「错题本」查看；错题重练答对即移出。
- 默认时长：语文标点PK、的地得PK 150 秒，其余语文游戏 180 秒；数学 150 秒。开始页右上角可设 30—1800 秒，开局后锁定。
- 计分（两站相同）：答对 +10，连对额外 +5，答错 −5（最低 0）。
- 时长与声音开关保存在浏览器 localStorage：语文 `chinese-pk-settings-v1`，数学 `math-pk-settings-v1`。
- 音效素材与许可见 `shared-static/game-audio-v3/SOURCES.md`。

## 个人资料、班级与排行
- 一个授权码就是一个用户。激活后首页右上角的「获取授权码」会换成头像按钮，点进「我的」（`/me`）可以改昵称（1—8 字）、换头像，以及创建或加入班级。未授权用户看到的首页不变。
- 头像在浏览器里先裁成 96×96，服务器再用 Pillow 重新编码成无元数据的 WebP（约 2—4 KB），存在 `profiles.avatar` 里；只有已授权用户能看。
- 每人最多在一个班级里（`profiles.class_id`）。建班的人是班主，可以改班名、解散班级、移出成员，也可以重置成员的昵称或头像；后台「授权详情」也能做这些操作。
- 排行榜分「本周」（北京时间每周一 0 点清零）和「累计」，按得分总和排名。单人练习每局结束时提交对错序列，由服务器按游戏规则重新算分；开局时领取的凭据只能用一次，答题太快（每题不到 2 秒）不计分，每人每天最多 30 局计入排行。错题重练、试玩和同屏双人PK不计分。
- 代码在 `core/social.py`，页面是 `core/templates/me.html`，样式是 `shared-static/social/social.css`。

## 联网PK（房间）
- 已授权用户在游戏开始页选「联网PK」：点「创建房间」得到 6 位房间码，用「复制邀请」发给同学；同学输入房间码加入，同班同学还能在「班级里正在等人的房间」里直接点「加入」。链接里带 `?room=<房间码>` 时会自动加入。
- 每个房间 2—10 人，只有房主能开始。开始后有 3 秒倒计时，所有人做同一套题（房主的浏览器按题型配额抽题，服务器检查题目 id 都在题库里），时长用房主设置的本局时长。
- 浏览器每 2 秒轮询一次房间状态（`version` 没变时只返回很短的响应）。答案在浏览器里判对错，然后按题号顺序逐题上报；服务器按游戏规则计分，同一个人两次答题至少间隔 1 秒。
- 时间到了，或者所有人都答完，房间就结束：每人的得分计入班级排行（mode=online），第一名另加 20 分，并列第一都加；每人每天最多 30 局联网PK计分。
- 房间状态存在 SQLite 的 `rooms`/`room_players` 里，所以发布和重启不会打断正在进行的房间。等待中的玩家 45 秒没有响应就会被移出；超过 2 小时的房间会在下次有人开房时清理掉。代码在 `core/rooms.py`，前端在 `shared-static/pk/pk-core.js` 的「online PK rooms」一节。

## 修改题库
题库的唯一来源是 `sites/<站点>/banks/<游戏>.json`（字段说明见 `tools/banks.py` 开头）。修改后运行：

```bash
python3 tools/banks.py --fix     # 格式化 JSON，并重新生成 docs/ 下的复核清单
python3 tools/banks.py --check   # 校验答案、选项、抽题配额和清单是否最新（测试和发布时自动检查）
```

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
先提交并 `git push`，再在开发机上运行：

```bash
deploy/deploy.sh            # 测试 → git archive 上传为新版本 → 发布前备份 → 原子切换 → 平滑重载 → 健康检查（失败自动回滚）
deploy/deploy.sh status     # 当前运行的版本和保留的版本
deploy/deploy.sh rollback   # 切回上一个版本
```

服务器目录：`/opt/candy-games/releases/<时间>-<提交>/`（保留最近 5 个，`REVISION` 记录提交号）、`venvs/<requirements哈希>/`（依赖不变就复用）、`current` 与 `venv` 两个软链指向正在运行的版本。只改代码时用 HUP 平滑重载；依赖或 systemd 配置变化时自动改为重启（约 1–2 秒）。

## 服务器运维
- 服务：`systemctl status candy-games`；日志：`journalctl -u candy-games`。
- 自动恢复：进程退出后 1 秒内由 systemd 重新拉起（不限次数）；`candy-games-watchdog.timer` 每 30 秒检查两个站点的 `/health`，卡死时自动重启服务；nginx 也设置了自动重启。
- 备份：`candy-games-backup.timer` 每天 03:15 备份到 `/var/lib/candy-games/backups/`，保留 30 天；每次发布前也备份一次。
- 异地备份：在开发机上运行 `deploy/pull-backups.sh`，把服务器备份（含 secret.env）拉到 `~/candy-games-backups/`；需要每天自动拉取时，按脚本开头的说明加入开发机 crontab。
- 管理命令（在服务器上）：
  ```bash
  cd /opt/candy-games/current
  sudo -u candy-games env $(cat /var/lib/candy-games/secret.env) GAME_DATA=/var/lib/candy-games /opt/candy-games/venv/bin/python manage.py summary
  # export [--site chinese|math] / backup / reset-password --site chinese|math
  ```
