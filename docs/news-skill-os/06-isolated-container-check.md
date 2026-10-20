# News Skill OS 隔离容器与 PostgreSQL 验证

日期：2026-10-20（北京时间）  
分支：`feature/news-skill-os` @ `d63d9eb`  
生产：未部署、未合并 main；线上仍是 `plus-api:audit-fixes-20261019`。

## 环境

- 服务器：`104.207.82.85`，临时 Docker Compose 项目 `news-skill-check`，不接 Caddy、不暴露公网端口。
- API 镜像：`plus-api:news-skill-check-d63d9eb`，从 `plus-api:audit-fixes-20261019` 叠加本分支 `jin10-server/app` 与 `pyproject.toml`，安装新增 `PyYAML>=6,<7`、`jsonschema>=4.23,<5`。
- PostgreSQL：临时 `postgres:16-alpine` 容器，独立空库，仅容器网络访问；API 端口 `127.0.0.1:18080`。
- 开关：`NEWS_SKILL_OS_ENABLED=false`；JWT 使用临时密钥。

## 结果

| 检查 | 结果 |
|---|---|
| 镜像构建 | 成功（补丁镜像，新增依赖安装成功） |
| Compose 文法 | `docker compose config -q` 通过 |
| 启动与健康 | API 健康 200，临时 Postgres healthy |
| 迁移 | `alembic current` = `0011 (head)`，未新增 migration |
| 旧接口 | 临时编辑账号访问 `/api/briefs/categories` 返回 200 |
| 开关关闭 | 同一编辑账号 `POST /api/news-skill-os/fixtures/official/run` 返回 **404** 与“News Skill OS 未启用” |
| 未登录 | 新路由返回 401（沿用现有 JWT 依赖） |
| 包数据 | 容器内 `python -m app.news_skill_os --fixture official` 成功执行 |
| 清理 | `docker compose down -v` 删除临时容器/网络/卷；临时镜像删除；生产 `plus-api`/`plus-brief-worker-1` 未受影响 |

## 仍未验证/未授权

- 未开启 `NEWS_SKILL_OS_ENABLED=true` 做生产运行；本分支仍不接生产任务、不自动发布。
- 未在线上部署本分支，也没有生产回滚演练；生产镜像和数据库仍是 `audit-fixes` 版本。
- 分支基于 `0a8c4fc`，`main` 还有 `b238d8a`（编辑可选 AI 引擎/模型）；合并前需把该提交合入并复跑测试。
- 本验证只覆盖默认关闭版本的镜像/Compose/PostgreSQL/迁移/旧接口/关闭路由/清理，不代表真实新闻抽取、在线模型或持久化事实链已验证。

## 后续：持久化 0012 的隔离验证

2026-10-20 在 `feature/news-skill-os` 的 `ae23c3c` 上另建临时项目，使用真实 PostgreSQL 和 `plus-api:news-skill-ae23c3c` 验证 `0012 (head)`、事件→修订→研究工作流→注解草稿→人工批准→导出链路；同时修复了 PostgreSQL 外键顺序问题（evidence/claims 必须先于 link 插入）。详见[持久化基础](07-persistence-foundation.md)。生产仍未部署。
