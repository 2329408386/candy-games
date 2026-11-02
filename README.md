# candy-games：语文 / 数学 / 英语小游戏

一个服务同时提供三个站点（语文、数学的网址与合并前完全一致）：

| 站点 | 网址 | 后台 |
|---|---|---|
| 语文 | `http://39.106.16.99/` | `/admin` |
| 数学 | `http://39.106.16.99/math/` | `/math/admin` |
| 英语 | `http://39.106.16.99/english/` | `/english/admin` |

## 结构
- `wsgi.py`：一个 gunicorn 进程里挂三个 Flask 站点（语文在 `/`，数学在 `/math`，英语在 `/english`）。
- `core/app.py`：共享的路由、授权、后台逻辑；`core/schema.py`：数据库表结构。
- `sites/<chinese|math|english>/`：站点配置 `sites/__init__.py`、题库 `banks/<游戏>.json`、游戏页外壳 `games/`、静态资源 `static/`、站点专属模板（首页、外框）。
- `core/templates/`：两站共用的后台模板。
- `shared-static/pk/`：各站共用的游戏引擎 `pk-core.js`/`pk-core.css` 和各题型渲染器 `types/`；`shared-static/game-audio-v3/`：共用音效。
- `tools/assets.py`：静态资源指纹；`tools/banks.py`：题库校验并生成复核清单；`tools/english_audio.py`：合成英语听力录音。
- `docs/`：由题库生成的复核清单（不要手改）。
- `deploy/`：systemd、nginx 配置和发布脚本。

各站各自保留独立的密钥（`CHINESE_SECRET`、`MATH_SECRET`、`ENGLISH_SECRET`）、Cookie 名（`session`、`math_session`、`english_session`）、管理员账号和授权编号。所有数据存在同一个数据库 `/var/lib/candy-games/games.sqlite3` 里，每张表都按 `site` 列区分站点。

## 游戏规则
- 语文、数学每款游戏题库 100 题，每局每人抽 20 题（按题型均衡抽取），同局双方不重复。可选双人PK、单人练习、错题重练。
- 英语（外研版三起 2024 三年级上册，A组 5 款：听音识词、情景选择、字母奇妙屋、句子拼图、读句子）：除字母奇妙屋外，题库按单元标 `unit`（U1—U6），开始页可选「期末综合」（各单元按 4/3 题混合抽 20 题）或单个单元（只抽该单元：单人 20 题，同屏双人平分、最多各 20 题）；联网PK 用房主选的单元。字母奇妙屋只考大小写互写，不播放读音、开始页醒目提示「用手写笔写效果最好」（游戏配置 `highlight`）；其余听音题带录音（`sites/english/static/audio/`），出题时自动播放一次，可点「再听一遍」重听；听音识词、读句子两人同时用喇叭或麦克风会互相干扰，所以没有同屏双人PK（配置 `"noPk": true`），比赛用联网PK。
- 字母奇妙屋（`types/handwrite.js`）：在英语四线格里手写字母（每格宽同田字格、高同英语本四线三格，第三线红色）。浏览器内识别：$P 点云比字形，再看字母在四线里的上下位置（大写占上两格、b d h k l 顶到第一线、g j p q y 到第四线），所以 C/c、O/o、P/p 这类能分开；I 和 l 都算对。`tests/test_handwrite.cjs` 用变形后的模板检查识别率。
- 读句子（`types/read.js` + `core/speech.py`）：每单元 20 句课内核心句，先听示范（Kokoro 合成），再点「开始读」录音（16 kHz PCM，最长 10 秒），服务器转给阿里云「一句话识别」，按读对的单词比例打分，80 分算对；缩写、mom/mum、数字等两种写法都算。浏览器只允许 https 页面用麦克风，http 页面会提示切换。阿里云配置只放在服务器 `secret.env`：`ALIYUN_AK_ID`、`ALIYUN_AK_SECRET`（需有智能语音交互权限）、`NLS_APPKEY`（项目识别模型须为英语 16k）；没配置时提示「语音评测暂未开通」。限流：每个授权每 2 秒 1 次、每天 300 次，试玩每天 25 次，全站同时最多 3 个评测。
- 题库只能在授权后通过 `/games/<游戏>/bank.json` 获取。
- 免费试玩：未授权的浏览器每个游戏可玩一局（点开始即用掉，记录在 `trials` 表，按会话里的设备令牌区分），试玩只下发够一局的题；结束后弹出管理员微信二维码（`shared-static/contact/`）。首页右上角「获取授权码」也会打开这个二维码。
- 游戏页右上角「投屏」：整体放大字号并全屏，适合教室投影；按 Esc 退出全屏时同时退出投屏。
- 答错的题记入本机错题本（localStorage `<站点>-mistakes-v1`，如 `chinese-mistakes-v1`，最多 300 条），首页右上角「错题本」查看；错题重练答对即移出。
- 默认时长：语文标点PK、的地得PK 150 秒，其余语文游戏 180 秒；数学 150 秒；英语字母奇妙屋 240 秒、读句子 300 秒，其余英语游戏 180 秒。开始页右上角可设 30—1800 秒，开局后锁定。
- 计分（两站相同）：答对 +10，连对额外 +5，答错 −5（最低 0）。
- 时长、声音开关和英语的单元选择保存在浏览器 localStorage `<站点>-pk-settings-v1`。
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
- 玩家按设备区分：同一个授权码的几台设备（比如家里的手机和平板）也能在同一个房间对战，列表里显示成「昵称 · 设备2」，房间列表里也能看到「我的另一台设备开的房间」。只有一个授权码的房间是自家练习赛，不计分、没有冠军奖励，等待页和结果页会提示邀请同学来比；混合房间里每个授权码只按得分最高的一台设备计一次分，冠军奖励也按账号比。
- 房间状态存在 SQLite 的 `pk_rooms`/`pk_seats` 里（旧的 `rooms`/`room_players` 按授权码区分玩家，已不再使用），所以发布和重启不会打断正在进行的房间。等待中的玩家 45 秒没有响应就会被移出；超过 2 小时的房间会在下次有人开房时清理掉。代码在 `core/rooms.py`，前端在 `shared-static/pk/pk-core.js` 的「online PK rooms」一节。

## 修改题库
题库的唯一来源是 `sites/<站点>/banks/<游戏>.json`（字段说明见 `tools/banks.py` 开头）。修改后运行：

```bash
python3 tools/banks.py --fix     # 格式化 JSON，并重新生成 docs/ 下的复核清单
python3 tools/banks.py --check   # 校验答案、选项、抽题配额和清单是否最新（测试和发布时自动检查）
```

英语题库新增或改动了带 `audio` 的题时，在开发机上合成录音（Kokoro-82M，安装方法见 `tools/english_audio.py` 开头），再加指纹：

```bash
<装了 kokoro 的 python> tools/english_audio.py   # 只合成还不存在的录音；--all 全部重新合成
python3 tools/assets.py --fix                     # 给新录音加哈希并改写题库里的文件名
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
- 新增站点（如英语站）：先在 `/var/lib/candy-games/secret.env` 加上该站的密钥（如 `ENGLISH_SECRET=<随机64位>`）再发布；发布后运行 `manage.py seed --site <站点>`，建好各学期「A组」并把管理员初始密码写到 `/var/lib/candy-games/<站点>/管理员初始凭据.txt`。
- https：Let's Encrypt 给 IP 签的短期证书（约 6 天），certbot 装在 `/opt/certbot`，`candy-games-cert.timer` 每天两次续期并重载 nginx；首次签发用 `/opt/candy-games/current/deploy/install.sh cert`。http 和 https 内容完全一样（共用 `deploy/nginx-locations.conf`），不强制跳转；授权 Cookie 两边通用，错题本和设置存在浏览器 localStorage、两边各一份，从 http 点「🔒 安全连接」或读句子时会带过去合并（`shared-static/social/carry.js`）。
- 登录：授权和后台登录的 Cookie 有效期 10 年、每次访问续期，相当于永久；后台改密码后其它设备的后台登录失效。
- 自动恢复：进程退出后 1 秒内由 systemd 重新拉起（不限次数）；`candy-games-watchdog.timer` 每 30 秒检查两个站点的 `/health`，卡死时自动重启服务；nginx 也设置了自动重启。
- 备份：`candy-games-backup.timer` 每天 03:15 备份到 `/var/lib/candy-games/backups/`，保留 30 天；每次发布前也备份一次。
- 异地备份：在开发机上运行 `deploy/pull-backups.sh`，把服务器备份（含 secret.env）拉到 `~/candy-games-backups/`；需要每天自动拉取时，按脚本开头的说明加入开发机 crontab。
- 管理命令（在服务器上）：
  ```bash
  cd /opt/candy-games/current
  sudo -u candy-games env $(cat /var/lib/candy-games/secret.env) GAME_DATA=/var/lib/candy-games /opt/candy-games/venv/bin/python manage.py summary
  # export [--site chinese|math] / backup / reset-password --site chinese|math
  ```
