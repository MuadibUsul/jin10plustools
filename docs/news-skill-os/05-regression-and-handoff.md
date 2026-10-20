# News Skill OS 回归、风险与交接

工作分支：`feature/news-skill-os`。本报告针对分支上的离线 MVP，**未部署、未合并 main**。基线、调用链与设计参见 [审计](01-repository-audit.md)、[基线](02-baseline.md)、[ADR](03-architecture-decision.md)、[协议说明](04-protocols-and-mvp.md)。

## 已执行的验证

| 检查 | 实际结果 | 边界 |
| --- | --- | --- |
| 旧服务端基线 | 接入前短临时路径：205 passed、4 skipped | 本机 Windows；首次常规临时路径的 7 项图片测试因路径过长失败，短路径重跑通过 |
| 新模块合同/权限/离线流程 | 新增 41 个测试通过 | 样例和 20 个主题组合为合成数据，无外部网络、模型或生产数据 |
| 完整服务端回归 | `pytest -q --basetemp E:\t3 --tb=short`：**246 passed、4 skipped、1 warning**，204.46 秒 | 包含旧测试和新增测试；1 个 Starlette/httpx 弃用警告 |
| 扩展端 | `npm test`：71/71；`npm run build`、`npm run lint` 均通过 | 扩展源码与 manifest 本分支未修改 |
| 包分发 | `pip wheel --no-deps` 成功；检查 wheel 包含 Manifest lock、Schema、流程 YAML、样例 JSON | 未在 Docker 镜像中验证 |
| Compose 文法 | 三份 Compose YAML 均可由 PyYAML 解析；API 环境段默认传 `NEWS_SKILL_OS_ENABLED=false` | 当前设备无 Docker，未执行 `docker compose config`、健康检查或线上运行 |
| Git 内容 | `git diff --check` 通过；新增文件在 `app/news_skill_os/`、隔离路由和测试 | 未作真实负载/性能测试 |

## 兼容性与安全结论

- **旧业务路径**：没有修改旧 API 方法、请求/响应 Schema、数据库模型/迁移、浏览器扩展 manifest/消息/存储键、固定快讯 worker 或日报生成器。`app/main.py` 只增加一条新命名空间路由。完整服务端与扩展测试在本机通过。
- **关闭开关**：`NEWS_SKILL_OS_ENABLED` 默认 `false`；新路由返回 404，测试确认没有向现有 `Material`、`Issue`、`BriefJob` 写入记录。Compose 配置显式透传且默认关闭。
- **打开开关**：只有编辑/管理员可运行三种仓库内离线 fixture；viewer 返回 403。响应固定禁止发布并要求人工审核；没有 CMS 发布代码、生产数据读取或外部请求。此开关不应在生产开启，直到真实业务接入和部署测试完成。
- **证据与审核**：错误数字为 rejected，匿名单一来源保持 unverified，审核返回 blocked；官方样例通过并保留 Claim/Evidence ID。五类历史固定快讯资产只作编辑规范与样本，旧数字不得进入新稿。
- **密钥与日志**：本模块无密钥字段，运行记录仅保存摘要、节点状态和异常字符串；当前 fixture 不含隐私数据。未来外部 Skill 的异常文本可能泄漏源内容，接入前需添加脱敏和限制；当前的受限入口白名单不是执行不可信代码的安全沙箱。

## 仍未通过的合并门槛

1. 已在服务器临时项目 `news-skill-check` 验证镜像构建、Compose 文法、真实 PostgreSQL、`alembic current=0011 (head)`、健康、关闭开关 404、旧接口 200 与清理；详见[隔离验证](06-isolated-container-check.md)。生产部署/回滚演练仍未执行。
2. NewsEvent/Claim/Evidence 和 DAG run 当前为**内存**实现；重启后不保留事实修订、审计或缓存。持久化版需要新表、事务、团队隔离、数据导出登记与迁移验证。
3. 五类固定快讯文字 Skill/100 条历史样本已入仓库，尚未转为运行时 Manifest/适配器；真实新闻抽取、可靠事实核查、付费/免费源权限、在线模型和编辑界面未实现。
4. 同步运行器的超时只在调用返回后检查，不能强制中断阻塞函数。若将来接网络或 LLM，必须采用可取消的隔离执行器，并补重试、并发、速率限制和持久化幂等测试。
5. 云端 feature 分支已确认为 `d63d9eb`；生产镜像仍是 `audit-fixes`，本分支未部署。旧系统在线表现只能通过部署前后对照确认，不能从本机单测推断。

## 回滚演练与实际操作

本机开关测试证明关闭 `NEWS_SKILL_OS_ENABLED` 后新路由不运行工作流且不写旧业务记录；完整回归覆盖旧路径。**这不是云端回滚演练**。若未来仅部署当前版本，在环境中设 `NEWS_SKILL_OS_ENABLED=false` 并重启 API 即可停用旁路；随后可回退本分支的新增提交。因本阶段没有 `news_*` 数据表，不涉及删表或清空卷。任何未来 migration 回滚应保留历史事实数据，不运行删除式 downgrade。

## 交接下一阶段

- 先在隔离容器/真实 PostgreSQL 环境验证当前默认关闭版本，再决定是否合并；不要直接开启生产开关。
- 把五类 `SKILL.md` 的文字规范转成独立 Manifest/适配器。输入只接受当次已验证的结构化数据；固定解释取团队可编辑配置，历史稿仅作为风格和回归样本。
- 建持久化前先评审数据模型和新增表，记录团队隔离、证据快照的版权/隐私边界、`data_export.py` 注册以及人工确认链路。
