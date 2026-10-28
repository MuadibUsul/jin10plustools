# 金十 PLUS 工作台

统一工作台部署于 https://plus.tlines.tech/ 。日报选稿位于 `/daily/`，固定快讯位于 `/briefs/`，另有 Plus新鲜事、Plus注解辅助、Skill 库和设置。`/test/` 已退役，不要用测试快照替换生产数据库或存储。

## 本地开发与验证

服务端位于 `jin10-server/`，浏览器扩展位于 `jin10-extension/`。Python 依赖见服务端 `pyproject.toml`，配置项见 `app/config.py`，不要提交真实密钥。

```powershell
cd jin10-server
.venv/Scripts/python.exe -m pytest -q
```

真实 PostgreSQL 检查需要将 `PLUS_TEST_POSTGRES_URL` 指向独立测试数据库。测试会创建并删除随机 schema，不应连接生产数据库。

```powershell
cd jin10-extension
npm ci
npm test
npm run build
npm run lint
```

## 构建和部署

Docker 构建上下文必须是仓库根目录，确保根目录的 `skills/` 同时进入镜像：

```sh
docker build -f jin10-server/Dockerfile -t plus-api .
```

根目录及 `deploy/` 中的 Compose 配置都使用这一构建方式。部署脚本为 `deploy/deploy.sh`；现有运行时的增量发布可使用 `deploy/Dockerfile.release`。API 与 brief-worker 必须使用同一发布镜像。升级使用 Alembic 增量迁移，保留生产数据库、存储卷和环境设置。

GitHub Actions 的 **Deploy PLUS** 工作流只允许在 `main` 分支手动触发。首次使用须在仓库 Actions secrets 配置 `PLUS_DEPLOY_SSH_KEY`（具有 `/home/deploy/plus` 与 Docker 权限的 `deploy` 用户私钥）和 `PLUS_DEPLOY_KNOWN_HOSTS`（核对过的服务器 SSH host key 行）。工作流先检查现有 PLUS Compose 项目，构建带提交 SHA 的镜像并备份数据库，然后仅更新 `api` 和 `brief-worker`；不会重建 `db`、共享 Caddy 或其他站点，也不会覆盖服务器的 `.env`、Compose 文件与数据卷。健康检查失败时回退到先前镜像。

## 生产约束

- 固定快讯由规则取数、计算、制图，再经 Skill 文案阶段。AI 只选择允许的表达，失败自动回退完整规则稿；稿件仍需人工确认。团队正文模板只支持命名占位值，不接受硬编码行情数值或执行代码。
- AI算力CPI、AI景气度指标仅手动生成，从授权归档数据取数，保留缺失日期、精度和修订。其他栏目的排期以服务端 policy 和团队设置为准。
- Plus新鲜事和 Plus注解辅助由 `NEWS_SKILL_OS_ENABLED` 控制；生产已启用，本地默认关闭。产物仅保存为待审核稿件，不自动发布。
- 所有最终图片经过 `app/services/image_delivery.py`，使用原始 PLUS 水印，最多 266,000 字节；下载名称使用生产日期和标题 MD5。估值图的既定布局保持不变。
- 稿件最多保留最新两版及已确认版；查看者只能看到已确认版。缓存必须匹配 pipeline/delivery 签名。
- 设置中的数据导出严格按团队隔离，所有登录成员均可使用。导出不包含密码、密钥、原始网页或来源二进制。算力修订及业务配置历史也在导出注册表中。
- 日报编辑和清空需携带 `If-Match` 版本；过期编辑返回 409，客户端应刷新后重新确认。
- 扩展离线队列按服务器和团队隔离，切换团队不会把旧团队未同步内容上传到新团队。

本次审查逐项记录见 [项目审查修复记录](docs/项目审查修复-20261023.md)。
