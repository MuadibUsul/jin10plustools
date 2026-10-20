# News Skill OS 仓库审计（只读阶段）

审计基线：`0a8c4fcaeb0af213da0a628118323b6491447062`，原分支 `integration/fixed-briefs`。审计日：2026-10-20。启动前 `git status --short` 仅有未跟踪的 `docs/jin10-plus-text-history/` 和 `skills/`；两者后经用户明确指定为本系统关键资产，以单独提交 `f7963a3` 迁入。此报告只依据本地源码，不代表云端运行版本。

## 已证实的系统结构

```text
金十网页 → MV3 contentScript / contextMenu → background messageRouter
        → LocalMaterialRepository / assetStore → syncService
        → serverApi.apiRequest（Bearer）→ /api/materials、/api/assets
        → Material / Asset（团队隔离，PostgreSQL）

选稿网页 /daily/ → /api/board、/api/pool、/api/issues
        → issue_builder → PPTX / PDF → storage 卷

固定快讯网页 /briefs/ → /api/briefs/jobs
        → PostgreSQL BriefJob → 独立 brief-worker → briefs.runner / 来源 / 制图
        → BriefIssue / BriefVersion / BriefSourceSnapshot → 编辑确认
```

- **服务端入口和网页**：`jin10-server/app/main.py:lifespan` 在 API 进程中启动 `AnalysisWorker`（由现有 AI 配置控制）和 `MaintenanceWorker`；`app.include_router` 注册现有 API；`/`、`/daily/`、`/briefs/` 分流，最后挂载静态网页。新增旁路 API 应在静态挂载前注册，且不改变这些原路径。
- **鉴权与权限**：`app/api/auth.py:login` 创建 JWT；`app/deps.py:get_current_user` 读取 Bearer token 并检验用户是否有效；`require_editor` 限制写入。`User.team_id` 是现有团队隔离边界。新入口如使用现有账号，需复用这些依赖，并逐条按团队过滤，不依赖客户端传来的 team ID。
- **插件协议**：`jin10-extension/manifest.json` 是 MV3；`src/background/serviceWorker.ts` 顶层注册消息监听、同步 alarm；`src/background/messageRouter.ts:backgroundHandlers` 定义消息处理；`src/types/messages.ts:MessageMap` 为类型契约。`src/services/api/serverApi.ts` 使用 `/api/auth/login`、`/api/materials/sync`、`/api/materials/changes`、`/api/assets/*`；`src/services/api/apiClient.ts:apiRequest` 附 Bearer，401 清除会话。`src/shared/constants.ts:STORAGE_KEYS` 定义已有持久化键。首版 News Skill OS 不要求改插件，避免扩展权限、消息或存储键变化。
- **日报业务**：`app/api/materials.py:push/changes` 调用 `app/services/material_sync.py:push/changes`；`app/api/board.py:apply_ops` 管团队选稿板；`app/api/issues.py:create_issue` 经 `app/services/issue_builder.py:build_issue` 生成 PPTX/PDF。现有 `/daily/` 页面消费这些契约。
- **固定快讯业务**：`app/api/briefs.py:jobs/detail/confirm` 管任务、版本和人工确认；`app/briefs/worker.py:schedule/claim/execute` 在独立 Compose `brief-worker` 中运行，并可能发起外部取数和可选 AI 请求。`app/briefs/runner.py:generate` 负责类别生成；确认的成品不能被未确认版本替代（见仓库 `AGENTS.md`）。此流水线不是 News Skill OS 的默认执行入口；避免把新闻研究功能塞入既有定时任务。
- **已有后台副作用**：`app/ai/worker.py:AnalysisWorker` 可调用 JEV；`app/services/maintenance.py:MaintenanceWorker` 可清理超期素材和无引用图片；`app/briefs/worker.py` 可自动排期并运行取数、AI、渲染。新功能关闭时不得新增 worker、调用外部模型或创建新业务记录。
- **数据库与存储**：`app/db/models.py:Base` 包含 `teams/users/materials/assets/issues/boards/parse_reports/brief_configs/brief_issues/brief_versions/brief_jobs/brief_source_snapshots/brief_audits/feedback`；跨团队关联使用 `team_id`。`app/db/session.py:get_db` 请求结束提交，异常回滚。迁移从 `0001` 至 `0011`，入口 `alembic/env.py`；根 `docker-compose.yml` 有 `pgdata` 和 `storage` 命名卷；`jin10-server/Dockerfile` 启动前自动 `alembic upgrade head`。删除/重命名旧表会直接影响生产；本阶段不引入迁移。
- **结构化导出**：`app/services/data_export.py` 显式列出可导出的团队数据集；将来若引入新业务表，须登记可导出的非敏感字段，避免导出密钥、原始 HTML 或付费来源快照。
- **部署**：根 `docker-compose.yml` 启动 db、api、brief-worker；`deploy/deploy.sh` 直接远程构建部署。仓库内未见 `.github/` CI 目录。此次工作仅在分支，不运行部署脚本。

## 兼容性清单与拟采用旁路

| 现有契约 | 源码证据 | 本阶段边界 |
| --- | --- | --- |
| 插件消息、manifest、storage 键 | `jin10-extension/manifest.json`、`src/types/messages.ts`、`src/shared/constants.ts` | 不修改 |
| 素材同步与图片接口 | `app/api/materials.py`、`app/api/assets.py` | 不修改路径、方法、响应 |
| 日报选稿/PPT/PDF | `app/api/board.py`、`app/api/issues.py`、`app/services/issue_builder.py` | 不改现有业务流 |
| 固定快讯日程、图文、确认 | `app/api/briefs.py`、`app/briefs/worker.py` | 不挂接现有 worker；五类文字 Skill 先作为规范资产 |
| 团队权限 | `app/deps.py`、`app/db/models.py:User` | 可在未来隔离路由复用 |
| 数据卷/迁移 | `docker-compose.yml`、`alembic/versions` | MVP 不修改卷或旧表 |

## 风险与待证实

1. **测试环境与线上版本**：本地基线来自 `integration/fixed-briefs`，并非云端镜像核验；不能宣称覆盖生产镜像。服务端测试结果另记在 `02-baseline.md`。
2. **无现成 News OS 表**：领域模型、历史事实修订、DAG run 的持久化需要设计。先做纯模型与隔离存储；任何生产迁移应单独评审，并遵守 `data_export.py` 注册要求。
3. **线程/定时作业副作用**：API lifespan 已有维护线程，Compose 另有 `brief-worker`。新能力不能在 import、lifespan、默认配置中启动扫描或外部请求。
4. **素材生命周期**：`MaintenanceWorker` 默认按保留期清理原始素材；将来 News OS 引用源材料时，不能只保存旧 Material ID，需有可回溯但受权限和导出规则约束的证据快照。
5. **外部数据与判断**：五类历史快讯含当时的数字和解释；其中已有口径/单位差异，见 `docs/jin10-plus-text-history/index.md`。它们仅作措辞规范和回归样本，不是新稿的事实依据。
6. **本地部署验证范围**：当前机器没有 Docker 命令，故 Compose 健康和 PostgreSQL 迁移未在本次本机执行；不把 SQLite 单元测试等同于 PostgreSQL 兼容验证。

## 仍未核实

- 线上正在运行的镜像、数据库 migration revision、外部 Caddy 配置与真实 API 响应，未连接线上读取。
- 任何新增 News OS 路由在生产权限、并发与负载下的行为，须在实现后测试。
- README 对部分运行细节可能滞后于代码；本报告以实际入口和符号为准。
