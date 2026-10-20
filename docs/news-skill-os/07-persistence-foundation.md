# News Skill OS 持久化事实链、草稿与工作流运行

日期：2026-10-20（北京时间）  
分支：`feature/news-skill-os`  
代码提交：`b93f16e`（持久化主体）、`ae23c3c`（PostgreSQL 外键顺序修复）  
生产：2026-10-20 已部署 `plus-api:news-os-live-20261020`，生产迁移为 `0012`，`NEWS_SKILL_OS_ENABLED=true`；本文前面的隔离验证记录保留为当时的验证证据。

本阶段把离线 MVP 的“内存事实链”落成独立的、团队隔离的数据库基础设施。它是 Plus新鲜事、Plus注解辅助和后续研究型快讯的底座，不是五个固定快讯文字预览的替代品。固定快讯生产任务、日报、插件和旧 API 没有改为调用它。

## 新增表和不可变边界

迁移 `0012_news_skill_os` 只新增 `news_*` 表，不改旧表、不导入旧数据、不创建发布路径：

| 表 | 用途 | 关键约束 |
| --- | --- | --- |
| `news_events` | 团队事件身份 | `(team_id, event_key)` 条件唯一；`current_revision` 乐观并发 |
| `news_event_revisions` | 完整事实快照 | `(event_id, number)` 唯一；快照 SHA-256；只追加 |
| `news_event_evidence` | 修订级证据和来源归属 | `(revision_id, evidence_key)` 唯一；保存 `original_id` 与来源类型 |
| `news_event_claims` | 原子 Claim 与数值口径 | `(revision_id, claim_key)` 唯一；状态受 CHECK 约束 |
| `news_event_claim_evidence` | 支持/矛盾关系 | `(claim_id, evidence_id)` 唯一；外键到修订内的证据行 |
| `news_event_analyses` | 前因/后果/补充说明 | 必须声明 Claim/Evidence 依赖；事实/推断标签受约束 |
| `news_drafts` | Plus新鲜事/注解草稿当前头 | `target` 仅 fresh/annotation/brief_text/research；状态不含“已发布” |
| `news_draft_revisions` | 不可变草稿版本 | `(draft_id, number)` 唯一；审核元数据只写当前版本 |
| `news_skill_versions` | 团队首次使用的 Skill 快照 | `(team_id, skill_id, version)` 唯一；同版本摘要冲突拒绝 |
| `news_workflow_runs` | DAG 运行 | 运行键唯一；记录事件修订、输入/输出摘要和状态 |
| `news_workflow_nodes` | 节点级运行和缓存信息 | `(run_id, node_id)` 唯一；记录 Skill 版本、耗时、尝试、错误 |
| `news_audit_events` | 人工写操作审计 | 只追加；按团队、对象和时间查询 |

所有表都带 `team_id`；事件、修订、证据、Claim、草稿和运行的读取入口都以登录用户的 `team_id` 过滤。跨团队按不存在处理（404），不泄露对象是否存在。

### 事实修订规则

- 第一次修订必须 `expected_previous=0`；之后必须等于当前修订号。
- 事件行用 `UPDATE ... WHERE current_revision = expected_previous` 抢占；并发写入只有一个成功，另一个返回 409。
- 旧 Claim 不能从后续修订中消失；事实修正必须换新 Claim ID，并用 `superseded_by` 标记旧 Claim。
- 状态只能沿 `unverified/corroborated/verified/disputed/rejected/superseded` 的显式图移动；审核原因必须非空。
- 每个修订保存完整快照和 SHA-256，旧文章可按修订号重建；证据、Claim、分析同时有修订级查询行。
- 同一原始来源的转载共享 `source_original_id`，不会被当作两个独立来源。

### 草稿与人工审核

- 草稿当前头可更新，正文历史在 `news_draft_revisions` 中不可变。
- 每次修订使用 `expected_revision` 乐观并发；旧修订的标题、正文和审核记录保留。
- `草稿 → pending_review → approved/rejected` 都有审计；驳回必须填原因；批准后不能再次批准。
- **没有发布端点、CMS 调用或自动发布节点。** 审核通过只表示可进入后续人工取稿流程，不等于对外发布。
- 工作流运行成功后可由 `analysis` 节点生成草稿：
  - `fresh`：使用分析的 `draft.title/body` 核对后的事实正文；
  - `annotation`：把前因/后果/补充说明拼成待编辑的三段注解；
  - 生成后仍是 `draft`，必须人工提交和审核。

### Skill 与工作流

- 首次引用 `skill_id@version` 时把 Manifest、摘要和来源写入 `news_skill_versions`；同一版本内容变化拒绝复用。
- 研究 DAG 可以读取**已持久化的事件修订**：适配器只把有 supporting evidence 的 Claim 送入原文断言，把有依赖的三段分析送入背景输入；事实核查节点重新检查来源/证据，不信任调用方状态。
- 运行记录包含 `run_key`、工作流摘要、事件/修订、输入摘要、节点状态、缓存、重试、来源快照和错误；没有默认常驻 worker，也不会自动运行。
- 当前 Skills 仍是确定性的本地 fixture/规则实现；真实新闻抽取、在线模型和自动事实核查尚未接入。

## API（均受 `NEWS_SKILL_OS_ENABLED` 保护）

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| GET | `/api/news-skill-os/skills` | 登录成员 |
| GET/POST | `/api/news-skill-os/events` | 读 / 编辑 |
| GET | `/api/news-skill-os/events/{id}` | 登录成员 |
| GET | `/api/news-skill-os/events/{id}/revisions/{number}` | 登录成员 |
| POST | `/api/news-skill-os/events/{id}/revisions` | 编辑 |
| POST | `/api/news-skill-os/events/{id}/workflows/research/runs` | 编辑 |
| GET | `/api/news-skill-os/drafts` | 登录成员 |
| POST | `/api/news-skill-os/drafts` | 编辑 |
| GET | `/api/news-skill-os/drafts/{id}` | 登录成员 |
| POST | `/api/news-skill-os/drafts/{id}/revisions` | 编辑 |
| POST | `/api/news-skill-os/drafts/{id}/submit` | 编辑 |
| POST | `/api/news-skill-os/drafts/{id}/review` | 编辑 |
| GET | `/api/news-skill-os/audit` | 登录成员 |
| GET | `/api/news-skill-os/workflow-runs` | 登录成员 |
| GET | `/api/news-skill-os/workflow-runs/{id}` | 登录成员 |
| POST | `/api/news-skill-os/workflow-runs/{id}/drafts` | 编辑 |

旧的 `/fixtures/*/run` 与 `/fixed-briefs/*/preview` 保持原行为，不写数据库。开关关闭时以上新路由统一 404；旧 `/api/briefs/*`、日报和插件行为不变。

## 数据导出

`app/services/data_export.py` 新增 12 个团队隔离数据集，总计 23 个：

`news_events`、`news_event_revisions`、`news_event_evidence`、`news_event_claims`、`news_event_claim_evidence`、`news_event_analyses`、`news_drafts`、`news_draft_revisions`、`news_skill_versions`、`news_workflow_runs`、`news_workflow_nodes`、`news_audit_events`。

不登记密钥、密码哈希、原始网页 HTML 或来源快照二进制。证据表只导出摘录、来源字段和校验值。

## 验证记录

| 检查 | 结果 |
| --- | --- |
| 服务端完整回归 | `267 passed, 4 skipped`（SQLite 测试已启用 `PRAGMA foreign_keys=ON`，用于模拟 PostgreSQL 外键） |
| 持久化/API/导出专项 | 54 passed（含团队隔离、并发修订冲突、不可变历史、审核门禁、工作流草稿、导出） |
| SQLite 迁移 | `alembic upgrade head`、`alembic check`（无差异）通过 |
| 空库回退 | `downgrade 0011` 删除空 `news_*` 表；再次升级通过 |
| 有数据回退保护 | 写入一条事件后 `downgrade 0011` 抛出“contains historical data”，不删事实链 |
| 隔离 PostgreSQL/Docker | 镜像 `plus-api:news-skill-ae23c3c`；`alembic current=0012 (head)`；健康 200；旧接口 200；关闭开关 404（关闭路由在 `b93f16e` 构建验证，`ae23c3c` 仅修复写入顺序） |
| 隔离 PostgreSQL 写入链 | 创建事件 → 修订 1 → 事件研究工作流（succeeded）→ 由 analysis 生成注解草稿 → 人工批准 → 导出 `news_events` 均成功 |
| 已发现并修复 | PostgreSQL 外键要求先插入 evidence/claims 再插 link；SQLite 默认不校验外键曾掩盖该顺序问题，已修复并开启测试外键 |

隔离项目使用独立空 PostgreSQL、临时 JWT 和不接 Caddy 的端口；验证结束后删除临时容器/卷/镜像。生产 `plus-api` 与 `plus-brief-worker-1` 未受影响。

## 仍未完成

1. 生产部署已完成（见[产品化上线记录](../NewsSkillOS产品化上线-20261020.md)）；仍需要在真实使用中观察 AI 质量、来源核验和回滚演练。
2. 真实新闻输入、自动事件识别、在线模型/检索和独立事实核查仍未实现；当前运行只使用仓库内样例或编辑器提交的结构化事实。
3. Plus新鲜事、Plus注解辅助的编辑界面尚未接入这些 API；页面仍是占位入口。
4. 新闻表的保留期限、证据版权边界、审核人是否需要与作者分离、生产并发/性能压测尚未决策。
5. 固定快讯的五个文字 Skill 仍是独立预览规范，未改为生产生成任务的执行器；这是有意的隔离，不是本阶段遗漏。

## 回滚

- 应用层：设置 `NEWS_SKILL_OS_ENABLED=false`，停用所有新路由；旧业务不需要迁移或删表。
- 代码层：回退本阶段提交；`news_*` 表可以保留，不影响旧表。
- 空库 schema 回退：`alembic downgrade 0011`；有事实数据时迁移会拒绝删除，必须保留表并回退应用。
- 不执行 destructive migration，不删除生产持久卷。
