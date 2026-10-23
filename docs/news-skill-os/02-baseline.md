# News Skill OS 开工前基线验证

基线 commit：`0a8c4fcaeb0af213da0a628118323b6491447062`。验证设备：Windows / PowerShell，2026-10-20。测试命令未连接生产库、真实 LLM 或外部新闻 API。

| 检查 | 命令 / 条件 | 结果 |
| --- | --- | --- |
| 扩展依赖 | `npm ci --ignore-scripts`（`jin10-extension/`） | 成功安装 253 包；安装器报告 1 个 high 级依赖告警，未运行 `npm audit fix` |
| 扩展测试 | `npm test` | 10 个文件、71 个测试通过 |
| 扩展构建/类型 | `npm run build` | `tsc --noEmit` 和 Vite 构建通过 |
| 扩展 lint | `npm run lint` | 通过 |
| 服务端测试（普通路径） | `.venv-news/Scripts/python.exe -m pytest -q`（`jin10-server/`） | 198 通过、7 失败、4 跳过；7 个失败都发生于 Windows 长临时路径下的固定快讯图片交付，`image_delivery.py:store_image` 写入过长的临时文件名报 `FileNotFoundError` |
| 服务端失败项复核 | `pytest -q --lf --basetemp E:\t` | 原 7 个失败项全部通过，证实为本机临时路径长度问题，未修改业务代码 |
| 服务端完整短路径基线 | `pytest -q --basetemp E:\t2 --tb=short` | **205 通过、4 跳过、1 个 Starlette/httpx 弃用警告**，耗时 275.45 秒 |
| Docker / PostgreSQL | `docker --version` | 此设备无 Docker 命令，无法本机验证 Compose 启动与 PostgreSQL 迁移 |
| CI | 检查 `.github/` | 仓库中不存在该目录；不能宣称有 CI 保护 |

测试执行依赖原仓库的 `jin10-server/pyproject.toml` 与 `jin10-extension/package-lock.json`。Python 虚拟环境、Node `node_modules` 和构建产物不进入 Git。为避免 pytest 重用并递归清理其他目录，运行前分别确认 `E:\t`、`E:\t2` 不存在。此处的 205 个通过属于新增模块接入前的**旧业务基线**；新模块另行测试。服务端依赖是当日解析的版本，仓库没有 Python lock 文件，后续仍需固定环境复核。
