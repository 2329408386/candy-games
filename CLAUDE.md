# candy-games

## 部署（每次改完都要让服务器生效）
- 生产服务器：`root@39.106.16.99`，目录 `/opt/candy-games/`（`current` 软链指向正在运行的版本）。
- 不要直接在服务器上改文件：服务器只运行 `deploy/deploy.sh` 发布的版本，手改的内容会在下次发布时被覆盖。
- 每完成一个用户请求的代码/内容改动后，按顺序执行：
  1. `git add` 相关文件，`git commit`，提交信息写清楚改了什么；
  2. `git push`；
  3. `deploy/deploy.sh`（脚本会先跑 `tools/assets.py --check`、`tools/banks.py --check` 和 `pytest`，再上传、切换、健康检查，失败自动回滚）；
  4. 向用户报告结果：成功时给出新版本号；失败时贴出输出并说明是否已自动回滚。
- 不要用 `FORCE=1` 或 `SKIP_TESTS=1` 绕过检查，除非用户明确要求。
- 只是回答问题、或改动还没完成的中间步骤，不部署；用户说"先别部署"时跳过。
- 查看线上版本：`deploy/deploy.sh status`；回滚到上一版本：`deploy/deploy.sh rollback`（回滚前先征得用户同意）。
