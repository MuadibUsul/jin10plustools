# News Skill OS 回归、风险与交接

工作分支：`feature/news-skill-os`。本报告针对分支上的离线 MVP，**未部署、未合并 main**。基线、调用链与设计参见 [审计](01-repository-audit.md)、[基线](02-baseline.md)、[ADR](03-architecture-decision.md)、[协议说明](04-protocols-and-mvp.md)。

## 已执行的验证

| 检查 | 实际结果 | 边界 |
| --- | --- | --- |
| 旧服务端基线 | 接入前短临时路径：205 passed、4 skipped | 本机 Windows；首次常规临时路径的 7 项图片测试因路径过长失败，短路径重跑通过 |
| 新模块合同/权限/离线流程 | 新增 41 个测试通过 | 样例和 20 个主题组合为合成数据，无外部网络、模型或生产数据 |
| 完整服务端回归（持久化前） | `pytest -q --basetemp E:\t3 --tb=short`：**246 passed、4 skipped、1 warning**，204.46 秒 | 包含旧测试和新增测试；1 个 Starlette/httpx 弃用警告 |
| 完整服务端回归（持久化后） | `pytest -q --basetemp E:\t11 --tb=short`：**267 passed、4 skipped、1 warning**，195 秒 | SQLite 测试开启 `PRAGMA foreign_keys=ON`；1 个 Starlette/httpx 弃用警告 |
| 持久化 PostgreSQL 隔离链路 | 迁移 `0012 (head)`；事件→修订→研究工作流→注解草稿→人工批准→导出均通过 | 独立空库、临时 JWT、无 Caddy；修复了 SQLite 外键关闭掩盖的 link 插入顺序问题 |
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

## 后续仍需验证的门槛（生产已于 2026-10-20 部署）

1. 已在服务器临时项目 `news-skill-check` 验证默认关闭镜像（`0011`）和持久化镜像（`0012`）的构建、Compose、真实 PostgreSQL、健康、关闭开关 404、旧接口 200；持久化写入链另见[持久化基础](07-persistence-foundation.md)。生产部署/回滚演练仍未执行。
2. 持久化已落地 12 张 `news_*` 表、团队隔离、不可变事件/草稿修订、Skill 版本、运行/节点和审计，并登记 12 个导出数据集；旧表未改。仍需评审新闻数据保留期限、证据版权边界和审核人分离策略。
3. 真实新闻抽取、付费/免费源权限、在线模型、自动事实核查和 Plus新鲜事/Plus注解编辑界面仍未实现；当前只能运行仓库样例或编辑器提交的结构化事件。
4. 同步运行器的超时只在调用返回后检查，不能强制中断阻塞函数。若将来接网络或 LLM，必须采用可取消的隔离执行器，并补重试、并发、速率限制和持久化幂等测试。
5. 生产已于 2026-10-20 部署 `plus-api:news-os-live-20261020`、迁移 `0012`、开关打开；仍需观察真实链接抓取/AI 注解质量、来源核验和回滚演练。

## 回滚演练与实际操作

本机开关测试证明关闭 `NEWS_SKILL_OS_ENABLED` 后新路由不运行工作流且不写旧业务记录；完整回归覆盖旧路径。**这不是云端回滚演练**。若未来仅部署当前版本，在环境中设 `NEWS_SKILL_OS_ENABLED=false` 并重启 API 即可停用旁路；随后可回退本分支的新增提交。因本阶段没有 `news_*` 数据表，不涉及删表或清空卷。任何未来 migration 回滚应保留历史事实数据，不运行删除式 downgrade。

## 交接下一阶段

- 先评审持久化数据模型、新闻表保留期限、证据版权/隐私边界和审核人分离策略，再决定是否合并；不要直接开启生产开关。
- 把五类 `SKILL.md` 的文字规范接入可替换的运行时节点；输入只接受当次已验证的结构化数据，固定解释取团队可编辑配置，历史稿仅作为风格和回归样本。
- 在持久化 API 之上实现 Plus新鲜事/Plus注解辅助的编辑与审核界面；当前页面仍是占位入口，不要声称已完成。
- 真实新闻输入和在线模型接入前，先完成可取消执行器、来源权限、速率限制、密钥脱敏和人工确认链路的独立评审。
