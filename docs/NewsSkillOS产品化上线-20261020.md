# News Skill OS 产品化上线（2026-10-20）

## 已上线

### Plus新鲜事：一个链接先得到分析

`https://plus.tlines.tech/fresh/`

- 只需要粘贴一个公开新闻链接，可选填写关注焦点。
- 后端抓取公开网页（拒绝内网/localhost/非 HTTP(S) 地址），提取正文。
- AI 返回：
  - 时间线
  - 关键数字
  - 来源原话
  - 编辑判断（与信源原话分开）
  - 未核实/口径提醒
  - 预拟 400 字标题、电头正文和备注
- 编辑核对后点击“生成 400 字快讯”，才创建 Plus新鲜事草稿；草稿进入送审/通过/驳回。
- 正文按规则包含电头【地点】、不超过400字、备注另起一段；单一来源和未核实项由 AI/编辑标注。

### Plus注解辅助：一条快讯得到三段注解

`https://plus.tlines.tech/annotations/`

- 粘贴一条财经快讯原文，可选原文链接。
- AI 按 `实时财经新闻背景挖掘与因果分析助手` 要求输出普通文本：
  - 前因
  - 后果
  - 补充说明
- 三段合计通常300—450字、最多600字；结论前置，区分事实/估算/传闻/推断，禁止 JSON、代码围栏和来源清单。
- 页面展示后，编辑点击“保存为待审草稿”；进入人工审核，不自动发布。
- 服务端会做一次尽力而为的公开新闻 RSS 检索并把摘要交给 AI；检索失败时明确提示“仅基于原文，请人工核对”，不伪造联网核实。

## 后端与数据

- 迁移 `0012` 已在生产执行，12 张 `news_*` 表已创建；生产 `alembic current=0012 (head)`。
- `NEWS_SKILL_OS_ENABLED=true`，通过生产 Compose 注入。
- 新增 API：
  - `POST /api/news-skill-os/from-link/analyze`
  - `POST /api/news-skill-os/events/{id}/fresh-draft`
  - `POST /api/news-skill-os/annotations/analyze`
  - 既有事件/修订/草稿/审核/工作流 API。
- 所有数据按团队隔离；草稿审核仍为人工门禁，没有发布端点。
- 链接分析事件保存 claims/evidence 摘要和 AI 分析，不保存原始网页 HTML；注解草稿保存输入快讯、来源链接和三段正文。
- 数据库导出自动包含 12 个 `news_*` 数据集。

## 验证

| 检查 | 结果 |
| --- | --- |
| 服务端完整回归 | `291 passed, 4 skipped` |
| 前端完整回归 | `74 passed` |
| 生产迁移 | `0012 (head)`，12 张 news 表存在 |
| 生产开关 | 容器 `NEWS_SKILL_OS_ENABLED=true`，`get_settings().news_skill_os_enabled=True` |
| 生产页面 | `/fresh/`、`/annotations/` 200，包含链接/快讯输入和 NEWS SKILL OS |
| 生产 API | 登录后 `/api/news-skill-os/events`、`/drafts` 200 |
| 生产镜像 | `plus-api:news-os-live-20261020`，API 与 worker 同步 |
| 回归边界 | 固定快讯仍走 Skill 自动产出；旧日报、插件、固定快讯页面未改 |

## 已知边界

- 链接抓取仅支持公开 HTML 页面，付费墙/登录页/纯 JS 页面可能没有正文；错误会在页面提示。
- “联网核实”目前是公开新闻 RSS 的尽力检索，不是付费数据库或人工事实核查；AI 可能遗漏/误读，必须由编辑核对一手来源后审核。
- AI 可能提出不准确的分析；草稿只能人工审核，不会自动发布。
- Plus注解输入以单条快讯为边界；不混入其他快讯事实是提示词约束，仍需人工审核。
- 站点监控推送、自动发布、实时搜索 API 付费接入和更强的来源去重仍属于后续。

## 回滚

```bash
cd /home/deploy/plus
cp .env.bak-news-os-live-20261020 .env
docker compose -p plus up -d --no-deps api brief-worker
```

回退到 `plus-api:news-os-link-20261020`（已有事件 API 和基础页面）或更早镜像。生产迁移 `0012` 为追加表；不要删除有数据的 `news_*` 表。
