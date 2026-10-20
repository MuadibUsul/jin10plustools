# News Skill OS 协议与离线 MVP

实现位置：`jin10-server/app/news_skill_os/`。研究型流程仍只处理仓库内三个离线样例；五类固定快讯文字 Skill 已作为独立规范资产纳入 `skills/`，并通过各自的 Manifest 接入隔离文字预览入口，**尚未接入生产固定快讯生成任务**。此处的“Skill”是服务端运行时节点协议，不等同于 Codex 本地 `SKILL.md` 文件。

## `news.skill/v1`

`skill-manifest.schema.json` 定义 YAML Manifest 的 ID、SemVer、归属、描述、受限入口、输入/输出 JSON Schema、模型配置、超时、重试、幂等、工具权限、质量门槛和评测标识。`manifest.py:SkillRegistry` 校验字段、嵌套 Schema、依赖和授权，锁定 `(id, version)`；同版本内容冲突拒绝注册。十个内置 Manifest 的摘要存于 `manifest.lock.json`，防止发布后原地改写。入口仅允许 `app.news_skill_os.skills.*:run`，但这只是受控白名单，不是 OS 级沙箱；不能加载不可信 Python 代码。

当前全部节点仅允许 `local_fixture`。网络、数据库读写和发布权限默认不授予。Manifest schema 中存在这些权限位是为了显式拒绝和未来审查，不表示已经实现访问工具。

## NewsEvent / Claim / Evidence

`models.py` 定义 `NewsEvent`、`Source`、`Evidence`、`Claim`、`ClaimEvidenceLink`、`Analysis`、`Draft`、`ArtifactRevision` 和 `AuditEvent`。金额、比例等数值带单位、币种、统计口径、预测/实际和期间；同一原始来源的转载共享 `original_id`，不能冒充两条独立证据。`validate_revision` 阻止缺证据的 verified、冲突的已核实数字和缺事实依赖的分析；`EventLedger` 为线程安全、按团队隔离的内存追加式快照，修订需保留旧 Claim 和审计原因。它**不是数据库持久化**；重启后数据不保留。

## `news.workflow/v1`

`manifests/research-workflow.yaml` 定义 `extraction → verification → background → analysis → review`。`workflow.py` 在注册时检查节点、依赖、环、输入绑定和 JSON Schema 类型；运行时记录 run/node ID、Skill 版本、输入/输出摘要、时长、尝试次数和状态，确定性本地节点复用缓存。`invalidate_claims` 可按 Claim ID 清理依赖缓存；它只作用于当前进程内存。review 节点返回 `approved=false` 时整体状态为 blocked；没有发布节点。当前运行器为同步离线实现，超时是执行结束后的预算检查，**不能抢占阻塞中的 Python 函数**；未来允许外部工具前必须换成可取消的隔离执行器。

## 三个样例与执行

在 `jin10-server/`：

```powershell
python -m app.news_skill_os --fixture official
python -m app.news_skill_os --fixture rumour
python -m app.news_skill_os --fixture wrong-number
```

预期依次为 `succeeded`、`blocked`、`blocked`。三个样例均为**合成离线数据**，不代表实时财经事实。`tests/test_news_skill_os_workflow.py` 另用 20 个主题/来源/数字组合验证三段结构和审核门禁。这些用例锁定接口和保守处理行为，并不证明自动新闻研究质量。

## 可选 API 与开关

配置 `NEWS_SKILL_OS_ENABLED=false`（也接受 `PLUS_NEWS_SKILL_OS_ENABLED`）；各 Compose API 服务显式透传 `NEWS_SKILL_OS_ENABLED`，缺省仍为 `false`。唯一新增路由为 `POST /api/news-skill-os/fixtures/{official|rumour|wrong-number}/run`。未登录仍受现有 JWT 依赖保护；只读账号返回 403；开关关闭返回 404。即使样例审核通过，响应也固定 `publication_allowed=false`、`human_approval_required=true`。路由不落库、不调用旧 worker、不抓取在线素材，也不触发 CMS 发布。关闭功能无需迁移或删除表；不调用该路由时既有服务按原方式工作。

## 与五类固定快讯资产的关系

`skills/jin10-plus-*-text/SKILL.md` 保存五类标题/文段规范，每类的 `references/history.md` 和 `docs/jin10-plus-text-history/` 保存 20 条历史样本。`news.fixed-{volume,turnover,valuation,liquidity,oil-etf}@0.1.0` Manifest 记录各自规范和历史样本的 SHA-256，测试将校验这两个源文件；修改编辑资产后须发布新的 Manifest 版本，不可悄悄沿用旧锁值。

五类适配器只接受 `data_date`、按字段命名的 `facts` 和团队固定解释。每个数值事实必须带 `value`、精确 `unit`、`as_of`、`evidence_id` 及 `verified=true`；当前值日期必须等于 `data_date`，前值日期必须更早。缺失必需值、未核实值、非有限数、口径或单位不匹配会拒绝生成。巴菲特指数仅接受 `all_a_total_market_cap_over_previous_year_gdp` 口径；换手率明确写 9 日均线；流动性没有政策利率证据时不判断当前宽松/偏紧。历史样本不会作为当日事实输入或运行时数据源。此入口信任调用方提供的 `verified` 和证据 ID，**尚未独立核查源文件或在线数据**，因此只能作为编辑预览，不能作为自动发布门禁。

可选旁路入口为 `POST /api/news-skill-os/fixed-briefs/{category}/preview`，请求只含 `data_date` 和 `facts`。端点按登录团队从现有 `BriefConfig.notes` 读取可编辑固定解释，缺省使用该栏默认值；调用方不能覆盖。结果分别给出 `title`、`dynamic_body`、`fixed_note`、合成 `body`、注释放置方式和证据 ID。换手率及估值的注释放图片，正文仅含动态事实；原油 ETF 的固定结尾内联。此端点无图片生成、数据库写入、联网取数和发布操作，固定返回 `publication_allowed=false`。生产任务还未改为调用它；现有五类快讯流程继续独立运行。

## 回滚和未覆盖范围

当前代码级回滚可保持 `NEWS_SKILL_OS_ENABLED=false`，然后回退本分支的 News OS 提交；没有 `news_*` 数据表、生产任务或发布副作用可清理。完整 PostgreSQL 迁移、真实来源核验、在线模型适配、并发取消、跨进程持久化和生产性能不属于这个离线 MVP；这些工作须在后续独立评审和验证。
