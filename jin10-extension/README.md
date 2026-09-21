# PLUS Research（金十 PLUS 日报素材助手）

Chrome 插件（Manifest V3），用于从金十数据网页采集新闻素材，作为「PLUS 日报」自动生产系统的数据入口：

```
金十快讯 → 右键采集整条组件 → 素材库（团队共享）→ AI 分类 / 评分
        → 按栏目配额选稿 → 一键生成 PLUS 日报 PPT
```

## 技术栈

TypeScript（strict）· React 19 · Tailwind CSS 4 · Vite 8 + @crxjs/vite-plugin · Vitest + jsdom

## 快速开始

```bash
npm install
npm run build        # 产物在 dist/
```

在 Chrome 中打开 `chrome://extensions` → 开启「开发者模式」→「加载已解压的扩展程序」→ 选择 `dist/` 目录。

开发模式（热更新）：`npm run dev`，然后同样加载 `dist/`。

| 命令 | 作用 |
|---|---|
| `npm run build` | 类型检查 + 生产构建 |
| `npm run test` | 单元测试（解析器使用真实金十 HTML 作为测试样例） |
| `npm run lint` | ESLint |
| `npm run icons` | 重新生成图标 |

## 目录结构

```
src/
├─ background/   Service Worker：消息路由（后续：右键菜单、同步、AI 队列）
├─ content/      仅注入 www.jin10.com
│  └─ parser/    selectors.ts（金十改版只改这里）· normalize.ts · jin10Parser.ts
├─ popup/        工具栏弹窗
├─ options/      设置页（团队服务端登录）
├─ components/   共享 React 组件
├─ services/     存储 / API（后续阶段）
├─ shared/       消息总线、时间、哈希、错误、日志
└─ types/        数据模型、消息协议、栏目定义
tests/           Vitest 单测与金十 HTML 样例
```

## 金十页面结构（2026-09-21 实测）

| 字段 | 选择器 / 来源 |
|---|---|
| 条目 ID、发布时间 | `.jin-flash-item-container#flash20260921170417801800`（ID 中含年月日时分秒） |
| 条目类型 | `.jin-flash-item.flash` / `.flash.is-vip` / `.article.is-important` |
| 标题 | 快讯 `.right-common-title`（约 1/3 快讯没有，用正文首句兜底）；文章 `.right-content_title` |
| 正文 | 快讯 `.flash-text`（去掉「金十数据X月X日讯，」与 `.jin-tag`）；文章 `.right-content_intro` |
| PLUS 标题 | `.plus-link-title`（`title` 属性） |
| PLUS 解读 | 点击 `.plus-link-remark` → 弹出同域 iframe `…/plus/index.html#/?id=N` → `.annotation-content .a2ui-preview`（模块：`.a2ui-plus-view__text` 观点、`.a2ui-observation__text` 要点、`.a2ui-module-footer__images img` 配图，URL 去掉 `/lite`）；页面 mousedown 关闭弹窗 |
| 配图 | `.right-pic img[data-src]`，去掉 `/ncover` 后缀为原图 |
| VIP 锁定 | `.item-right.need-unlock`，正文不完整，标记 `locked: true` |

## 右键采集流程

1. 在快讯任意位置右键 →「PLUS Research › 加入日报素材库」（或「加入并 AI 分析」）
2. 解析整条快讯组件（标题、正文、PLUS 标题、配图、发布时间、条目 ID）
3. 有 PLUS 注解时自动点开弹窗，读取渲染后的解读（「PLUS观点」「重点观察」等模块及配图），随后在页面空白处模拟按下关闭弹窗（约 1.5 秒）
4. 在金十页面内下载快讯配图与解读配图的原图
5. 交给后台入库：按条目 ID / 内容指纹去重；已存在时只补充缺失的解读或图片
6. 页面右上角提示结果，工具栏角标显示今日采集数

在 PLUS 注解弹窗内部右键不会采集（目标不明确）。

## 团队同步

1. 点击插件图标 →「登录团队」（或扩展的「选项」页），填写团队服务端地址、邮箱、密码
2. 登录时浏览器会询问是否允许访问该服务端地址——插件只申请这一个地址
3. 之后自动同步：采集后约 2 秒、每 5 分钟、浏览器启动时；弹窗中可「立即同步」
   - 先上传服务端缺少的图片，再推送本地素材，最后拉取团队其他成员的素材
   - 同事已收藏过同一条快讯时，合并为同一条素材并显示「N 人收藏」
   - 未登录时素材只保存在本机，登录后自动补传
   - 切换到另一个团队时，本机上一个团队的已同步素材会被清除

## 安全与隐私

- `host_permissions` 与 content script 仅限 `https://www.jin10.com/*`；不申请 `tabs`、`history`、`<all_urls>`。
- 团队服务端地址通过可选权限在登录时单独申请，只用于调用接口，不注入任何脚本。
- 只保存服务端签发的登录令牌，不保存密码；令牌失效时自动退出。
- 只采集用户主动选择的内容（右键 / 点击扫描），无后台自动抓取。
- PLUS 解读通过金十自身页面与用户本人的登录状态读取，不调用金十私有接口。
- 插件内不存放任何 AI Key；AI 与 PPT 生成全部在团队服务端完成。
- `raw_html` 保存前去除脚本、样式、SVG、iframe、注释与非必要属性，界面不以 innerHTML 渲染。

## 开发进度

- [x] **P1** 插件框架：工程、manifest、类型化消息总线、popup / 工作台骨架
- [x] **P2** 金十 DOM 解析：快讯 / VIP / 文章三类条目，线上 28/28 条解析通过
- [x] **P3** 右键采集：自动读取 PLUS 解读（点开注解弹窗 → 读取 → 关闭）、下载配图原图、去重合并、本地存储
- [x] **P4** 服务端（FastAPI + PostgreSQL + Docker）与团队同步：登录、素材推送 / 增量拉取、团队去重合并、图片上传
- [x] **P5** AI 分类与评分（服务端 TypeSafe JEV）
- [x] **P6** 选稿网页（服务端提供，插件弹窗「选稿网页 →」打开）：分栏目排序、勾选、调整栏目、按配额自动选稿、一键生成 PDF
- [x] **P7** PPT 引擎（见 ../jin10-server）：标准版式、按字数自动分页 / 调整字号
- [ ] **P8** PNG 导出、模板管理、权限
