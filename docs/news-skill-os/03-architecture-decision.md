# ADR 001：以旁路包引入 News Skill OS

状态：方案选定，实施与回归仍在推进。依据：[真实仓库审计](01-repository-audit.md)、[基线验证](02-baseline.md)。

## 问题和两个方案

当前仓库已运行日报选稿与固定快讯，API、扩展消息、PostgreSQL 表和两个现有 worker 均有稳定业务用途。News Skill OS 需要结构化证据、版本化 Skill 和 DAG，但不应借由新功能改变旧接口或默认任务排期。

| 方案 | 依赖与改动 | 隔离与代价 |
| --- | --- | --- |
| A. 服务端独立 `app/news_skill_os/` 包，先离线运行，后以开关保护的独立 `/api/news-skill-os/*` 接入 | 复用已有 Python、Pydantic、认证；少量新增 YAML/JSON Schema 验证依赖；不用修改插件 | 与旧 API 和 worker 隔离，最容易测试回滚；将来要落库需新增表/迁移 |
| B. 新 Docker 服务和独立数据库/队列 | 新 Compose 服务、网络、令牌、部署与监控 | 进程隔离更强，但维护面和故障点明显增加，不适合首个离线 MVP |

选择 **A**。先交付无网络、无数据库写入、无后台常驻任务的协议与离线工作流。将来接入 API 时，`NEWS_SKILL_OS_ENABLED` 默认 `false`；关闭时只存在受保护路由，不能运行 Skill、访问外部源、写入新业务数据或改旧表。研究草稿与人工审核完成前，不存在发布节点。

## 源码映射与旧文件变更界限

| 目的 | 路径 | 处理 |
| --- | --- | --- |
| Manifest、模型、DAG、离线 Skill | 新增 `jin10-server/app/news_skill_os/` | 独立模块，不 import 固定快讯 worker 或日报生成器 |
| 离线测试与 fixtures | 新增 `jin10-server/tests/test_news_skill_os*.py` | 使用内存输入与 mock，不连外部源 |
| 设计、测试证据、进度 | 新增 `docs/news-skill-os/` | 每阶段更新并推送 |
| 五类现有文字规范与 20 日样本 | `skills/jin10-plus-*-text/`、`docs/jin10-plus-text-history/` | 已作为参考资产迁入；未来显式适配到 Manifest，不能执行历史文案中的数字 |
| 可选配置 | `jin10-server/app/config.py`、三个 Compose API 环境段、`.env.example` | 仅新增默认 `false` 的布尔字段与透传；不改现有配置语义 |
| 可选路由 | 新增 `app/api/news_skill_os.py`，`app/main.py` 加一次路由注册 | 仅独立命名空间，沿用 `CurrentUser`/`Editor` 与既有异常风格 |
| 可选持久化 | 未来新增独立 `news_*` 表、Alembic additive migration、`data_export.py` 登记 | 先不触碰旧表；PostgreSQL 迁移需独立验证 |

## 三项协议边界

1. `news.skill/v1` Manifest 使用固定版本、输入/输出 JSON Schema、明确工具权限、超时、重试、幂等与评测标识。所有网络、数据库写入、发布权限默认为拒绝。注册表锁定 `(id, version)`，同键不同内容拒绝。
2. `NewsEvent / Claim / Evidence` 将可核验事实和编辑推断分开。来源链以原始出处标识去重；数值带单位、币种、统计期、比较基准和报道归属。状态变更追加审计事件，旧 revision 可重建。
3. `news.workflow/v1` DAG 在注册时检查环与依赖，执行时记录输入/输出摘要、状态、时间与错误。确定性节点可按输入与 Skill 版本缓存，外部副作用节点禁止盲目自动重试。人工审阅失败阻断下游。

## 认证、租户、安全与可观测性

- 首版离线库不暴露 HTTP；接入后只用 `/api/news-skill-os/`，读操作复用 `CurrentUser`，写/运行操作复用 `Editor`，所有查询以 `user.team_id` 过滤。`viewer` 不得触发运行；其他团队不可见。
- 请求只接受已校验 Schema，不允许 Manifest 直接 import 任意模块或声明未授权工具。错误消息可报告字段和节点，不回显凭据或原始私有材料。运行日志记录 ID、状态、摘要和耗时，避免全文。
- 不修改 Chrome manifest、`MessageMap`、`STORAGE_KEYS`，因此兼容矩阵为：旧客户端×旧服务端=原行为；旧客户端×新服务端且开关关闭=原行为；新客户端×新服务端=本阶段不适用（未开发新客户端）。
- 旧 API 行为必须由原有测试证明；开关关闭测试额外断言 News OS 不实例化执行器、不写入业务表。不能凭代码审阅宣称线上性能无影响。

## 数据落地与回滚

- MVP 先只在内存/fixture 上验证三项协议，不添加 migration、不执行在线发布。未来持久化设计须有 `team_id`、事务、唯一键、外键和必要索引；快照需屏蔽凭据及受版权限制的原始材料。新表进入 `data_export.py` 非敏感登记。
- 本分支可通过停止调用旁路入口和 `NEWS_SKILL_OS_ENABLED=false` 停用。若未来部署 additive migration，应用回退只停用代码和开关，保留 `news_*` 表数据；不得通过危险 downgrade 删除生产事实链。发布节点不在 MVP 范围。
- `docs/jin10-plus-text-history/` 中的旧例只用于判断文体、固定注释边界和测试回归。每日事实必须来自当次证据，固定解释内容需与动态正文分离，并遵守团队可编辑配置。

## 尚未验证的门槛

服务端完整 pytest、PostgreSQL migration、Docker 启动与线上镜像版本尚需实测。未通过前不合并或部署；本 ADR 不是部署批准。
