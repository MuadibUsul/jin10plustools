# PLUS 日报服务端

团队共享素材库、AI 分析与 PPT 生成服务（FastAPI + PostgreSQL，Docker 部署）。
已完成：团队素材库与同步（P4）、JEV 分类评分（P5）、选稿网页与一键生成 PDF（P6）、PPT 生成引擎（P7）。

## 使用流程

1. 在金十页面用插件右键采集快讯（普通快讯或带 PLUS 注解），自动同步到团队素材池
2. 服务端后台调用 TypeSafe JEV 为每条素材判断栏目并打分（素材文字原样保留，不做改写）
3. 浏览器打开服务端地址（如 `https://plus.example.com/`），登录后进入**选稿网页**：
   按 6 个栏目分组、按综合分排序；勾选要进日报的素材，可调整栏目（编辑指定后 AI 不再覆盖），或「按配额自动选稿」
4. 点击「生成 PDF」：按模板生成 PPT 并转换为 PDF，自动下载；生成记录可再次下载 PDF / PPT

## 部署（Docker）

```bash
cp .env.example .env            # 在仓库根目录；填写 POSTGRES_PASSWORD、PLUS_JWT_SECRET
docker compose up -d --build    # 启动时自动执行数据库迁移

# 创建团队与账号（密码交互输入）
docker compose exec api python -m app.cli create-team "PLUS 研究组"
docker compose exec api python -m app.cli create-user --team "PLUS 研究组" --email you@company.com --name 张三 --role admin
```

- 数据：PostgreSQL 数据卷 `pgdata`；素材图片数据卷 `storage`（`/data/storage`），备份这两个卷即可
- 团队外网访问请在前面加 HTTPS 反向代理（Nginx / Caddy），插件中填写 `https://` 地址
- 角色：`admin` / `editor` 可采集与同步；`viewer` 只读

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/login` | 邮箱 + 密码登录，返回访问令牌（默认 14 天） |
| GET | `/api/auth/me` | 当前账号、团队与成员 |
| POST | `/api/materials/sync` | 批量推送素材（≤50 条）；团队内按 条目 ID → 金十条目 ID → 内容指纹 去重合并，返回每条最终版本 |
| GET | `/api/materials/changes?cursor=` | 增量拉取团队变更（含删除），游标翻页 |
| GET / DELETE | `/api/materials/{id}` | 查看 / 软删除 |
| POST | `/api/assets/exists` | 批量查询服务端缺少的图片 |
| PUT / GET | `/api/assets/{id}` | 上传 / 下载图片（ID = sha256(原图地址)[:32]，团队内只存一份） |
| POST | `/api/materials/{id}/analyze` | 重新排队 AI 分析 |
| GET | `/api/pool?day=&days=` | 待选素材池（北京时间日期），按综合分排序 |
| PATCH | `/api/materials/{id}` | 调整栏目（锁定，AI 不再覆盖） |
| POST / GET | `/api/issues` | 用选中素材生成日报（PPTX + PDF）/ 生成记录 |
| GET | `/api/issues/{id}/file?format=pdf\|pptx` | 下载 |

## AI 分析（TypeSafe JEV）

每条素材一次请求，同时回答 5 个原子问题：栏目（Choice）、市场影响 / 新颖程度 / 参考价值（Score 0–3）、是否政策决定（Noul）。
代码中合成：重要度 = 影响 40% + 新颖 30% + 价值 30%（政策 +5）；综合分 = 重要度 60% + 团队热度 20% + 时效 10% + 完整度 10%；
等级 S ≥ 85 / A ≥ 70 / B ≥ 50 / C。权重与阈值可在 `teams.settings["scoring"]` 覆盖。
新素材、补全解读后自动分析；同事再次收藏只重算综合分。限流 / 超时自动重试，3 次失败标记为 failed。
实测：9/18 样例中四个新闻栏目全部分对；「精选观点 / 精选图表」是按形式划分的栏目，需编辑在选稿页调整。

## PDF

Docker 镜像内置 LibreOffice 与 Noto CJK（与思源黑体 / 思源宋体为同一设计），`docker/fonts.conf` 把模板字体名映射到对应字重。
Windows 开发机未安装 LibreOffice 时，自动调用本机 PowerPoint 导出。PDF 转换失败时仍可下载 PPT。

合并规则：已有 PLUS 解读不被覆盖；图片按地址合并；收藏人累加（团队热度）；AI 分类、评分等字段由服务端维护，客户端推送不会改写。

## 本地开发

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"
python -m pytest                       # 单元测试（SQLite，无需数据库）
alembic upgrade head                   # 需设置 PLUS_DATABASE_URL
uvicorn app.main:app --reload
```

## PPT 生成引擎

```
日报 JSON（栏目 → 素材：标题 / 新闻 / 解读 / 配图）
  → 分页：按标准版式网格排版，按字数自动续页、拆句、调整字号
  → 复制标准模板的原型页并填充
  → .pptx
```

### 命令行生成

```bash
python -m app.ppt.cli samples/2026-09-18/issue.json out/PLUS日报-2026-09-18.pptx
```

### 目录

```
app/ppt/
├─ layout.py            标准版式网格（边距、页眉页脚、正文区域、字号档位）
├─ models.py            输入模型 DailyIssue / DailySection / DailyItem（栏目与插件一致）
├─ textfit.py           正文排版测算（断行模拟：避头尾、标点悬挂、英文不拆词）
├─ paginate.py          分页与自适应
├─ generator.py         渲染 .pptx
├─ template.py          标准模板约定（原型页与形状名称）
├─ prepare_template.py  手工 PPT → 标准模板
├─ pptx_xml.py          复制 / 删除幻灯片等底层操作
└─ cli.py
templates/
├─ plus_daily_source.pptx     设计师手工制作的原稿
└─ plus_daily_template.pptx   标准模板（由 prepare_template 生成，生成器使用）
samples/2026-09-18/           由原稿还原的样例日报（含配图）
scripts/
├─ build_sample_issue.py      从原稿还原样例
├─ calibrate_textfit.py       测算校准（配合 measure_lines.ps1）
└─ qa_render.ps1              开发机上用 PowerPoint 渲染并检查溢出 / 重叠 / 越界
```

### 标准版式（竖版 15" × 26.67"）

原稿为手工制作，各页坐标有细微偏差（正文框宽 13.05" / 12.9" 不一、续页正文压住页眉线、页脚两行重叠等）。
生成时所有元素一律按 `layout.py` 的网格重新定位：

| 元素 | 位置 |
|---|---|
| 左右边距 | 1.0"，正文宽 13.0"，文字与图片左边缘对齐 |
| 页眉 | 0.68"；分隔线 1.37" |
| 栏目头「01 \| 宏观消息」 | 1.67"（仅栏目首页） |
| 正文区域 | 栏目首页 2.90" ~ 25.05"；续页 1.70" ~ 25.05" |
| 图文间距 | 0.30" |
| 页脚 | 免责声明 25.35"；网址 / 品牌 25.75" |
| 正文 | 思源宋体 CN Light 36pt，行距 170%，段后 12pt；标题 思源黑体 CN Bold |

### 按字数自动适配

- **自动续页**：超出正文区域自动续页；段落放不下时按句子拆到下一页（本页至少留 2 行）
- **标题不落单**：标题与下一段开头必须同页
- **自适应字号**：栏目最后一页内容不足 35% 时，尝试 34pt / 32pt，能少占一页则采用（同栏目统一，最小 32pt）
- **配图**：按正文宽度等比缩放；放不下时，剩余空间足够则缩小放入，否则换页；每条素材最多 2 张
- **文字原样使用**：标题、【新闻】、【解读】不做任何改写，版面适配完全由排版完成

测算精度：用 PowerPoint 实测 186 段样本（3 种宽度 × 2 档字号）校准，181 段行数完全一致，其余 5 段多估 1 行，**少估为 0**（只会多留空白，不会溢出）。

### 修改模板

设计师可直接修改 `plus_daily_source.pptx` 的配色、字体、装饰，然后重新生成标准模板：

```bash
python -m app.ppt.prepare_template templates/plus_daily_source.pptx templates/plus_daily_template.pptx
```

生成器只依赖形状名称（见 `template.py`），加载时会校验，缺失时给出明确错误。

### 字体

模板使用 **思源黑体 CN**（Bold / Normal / Light）与 **思源宋体 CN**（Light / Heavy），均为 SIL OFL 开源字体。
PPT 由查看者本机渲染；后续在服务端导出 PNG 时，Docker 镜像需安装这两套字体。

## 线上部署（plus.tlines.tech）

共享云服务器 `104.207.82.85`（用户 `deploy`），与 tlines.tech 等站点共用一个 Caddy（`/opt/caddy`，`caddy-caddy-1`）。

```bash
bash deploy/deploy.sh      # 本机直接打包上传、在服务器构建并滚动更新（不依赖 GitHub Actions）
```

- 服务目录 `/home/deploy/plus`：`plus-api` 与 PostgreSQL 不暴露任何端口，经外部网络 `web` 由 Caddy 反代并签发 HTTPS 证书
- 修改 Caddy 配置后，**不要**直接 `caddy reload --config /etc/caddy/Caddyfile`：Caddyfile 以单文件挂载，宿主机文件被整体替换过时容器内看到的是旧文件，会导致其他站点下线。应把宿主机最新文件送入容器再重载：
  ```bash
  docker exec -i caddy-caddy-1 sh -c 'cat > /tmp/Caddyfile.current' < /opt/caddy/Caddyfile
  docker exec caddy-caddy-1 caddy validate --config /tmp/Caddyfile.current && docker exec caddy-caddy-1 caddy reload --config /tmp/Caddyfile.current
  ```
