# News Skill OS 开工前基线验证

基线 commit：`0a8c4fcaeb0af213da0a628118323b6491447062`。验证设备：Windows / PowerShell，2026-10-20。测试命令未连接生产库、真实 LLM 或外部新闻 API。

| 检查 | 命令 / 条件 | 结果 |
| --- | --- | --- |
| 扩展依赖 | `npm ci --ignore-scripts`（`jin10-extension/`） | 成功安装 253 包；安装器报告 1 个 high 级依赖告警，未运行 `npm audit fix` |
| 扩展测试 | `npm test` | 10 个文件、71 个测试通过 |
| 扩展构建/类型 | `npm run build` | `tsc --noEmit` 和 Vite 构建通过 |
| 扩展 lint | `npm run lint` | 通过 |
| 服务端测试 | `python -m pytest -q`（`jin10-server/`） | 待完成：系统 PATH 无 Python；正在隔离的 `.venv-news` 安装项目依赖 |
| Docker / PostgreSQL | `docker --version` | 此设备无 Docker 命令，无法本机验证 Compose 启动与 PostgreSQL 迁移 |
| CI | 检查 `.github/` | 仓库中不存在该目录；不能宣称有 CI 保护 |

测试执行依赖原仓库的 `jin10-server/pyproject.toml` 与 `jin10-extension/package-lock.json`。Python 虚拟环境、Node `node_modules` 和构建产物不进入 Git。服务端测试结果完成后更新此表。
