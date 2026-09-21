/**
 * 金十页面 CSS 选择器。金十改版时只需修改本文件。
 * 结构来源：www.jin10.com 快讯列表（2026-09-21 实测）。
 *
 * .jin-flash-item-container#flash20260921162337xxxx  ← 条目 ID（含完整日期时间）
 *   .jin-flash-item.flash | .flash.is-vip | .article.is-important
 *     .flash-item-header .item-time span   ← 16:23:37
 *     .item-right.is-common[.need-unlock]  ← 主体（need-unlock = VIP 未解锁）
 *       快讯：
 *         .right-common-title              ← 标题（约 1/3 快讯没有）
 *         .right-vip .right-vip-title      ← PLUS 快讯（.flash.is-plus）的标题，前有 PLUS 徽章
 *         .flash-text                      ← 正文（可能内嵌 .jin-tag 标签）
 *         .flash-remark .plus-link-remark  ← PLUS 注解按钮（无 href，点击弹出 iframe）
 *           .plus-link-title               ← PLUS 标题
 *       文章：
 *         .right-content_title             ← .jin-tag + 标题
 *         .right-content_intro             ← 摘要
 *         .flash-remark a[href]            ← 详情页链接
 *       配图：.right-pic img[data-src]      ← 懒加载，URL 带 /ncover 缩略图后缀
 *             .right-pic .miniprogram-remark ← 小程序卡片（封面 + 标题 + 扫码查看），不采集
 */
export const SELECTORS = {
  flashItem: '.jin-flash-item',
  itemContainer: '.jin-flash-item-container[id^="flash"]',
  body: '.item-right',
  time: '.item-time span',
  title: '.right-common-title, .right-vip-title',
  content: '.flash-text',
  articleTitle: '.right-content_title',
  articleIntro: '.right-content_intro',
  tag: '.jin-tag',
  remark: '.flash-remark',
  miniprogram: '.miniprogram-remark',
  remarkLink: '.flash-remark a[href]',
  plusLink: '.plus-link-remark',
  plusTitle: '.plus-link-title',
  locked: '.need-unlock',

  /** 点击 PLUS 注解后弹出的 iframe 窗口（在页面上 mousedown 即关闭） */
  plusPopup: '.iframe-popup',
  plusIframe: '.iframe-popup iframe[src*="/plus/"]',
} as const;

/**
 * PLUS 注解页（同域 iframe：/feature/.../plus/index.html#/?id=N）结构，2026-09-21 实测：
 *
 * .annotation-content .a2ui-preview
 *   .a2ui-preview-header__content          ← 注解标题（与 .plus-link-title 一致）
 *   .a2ui-preview-module                   ← 模块（可多个）
 *     .a2ui-section-title                  ← 「PLUS观点」「重点观察」
 *     .a2ui-plus-view__text                ← 观点正文（段落以换行分隔）
 *     .a2ui-observation__row               ← 编号要点
 *       .a2ui-observation__badge / __text
 *     .a2ui-module-footer__images img      ← 配图（URL 带 /lite 后缀）
 *   .a2ui-preview-disclaimer               ← 免责声明
 * 加载中 / 失败：.annotation-state；未登录或无会员：路由跳转到 #/401
 */
export const ANNOTATION = {
  content: '.annotation-content',
  preview: '.a2ui-preview',
  title: '.a2ui-preview-header__content, .a2ui-preview-header',
  module: '.a2ui-preview-module',
  heading: '.a2ui-section-title',
  viewText: '.a2ui-plus-view__text',
  observationItems: '.a2ui-observation__items',
  observationText: '.a2ui-observation__text',
  paragraph: 'p',
  disclaimer: '.a2ui-preview-disclaimer',
  state: '.annotation-state',
} as const;

/** 非内容图片（会员标识、图标等），提取配图时排除 */
export const IGNORED_IMAGE_PATTERNS = [
  /\/assets\/img\/vip_logo\//,
  /\/assets\/img\/commons\//,
  /\/assets\/img\/a2ui-components\//, // 注解卡片的装饰插画
];

/** 金十图床的缩略图后缀（gimg 的 /ncover、img 的 /lite、flash-scdn 的 /flash），去掉即为原图 */
const THUMBNAIL_SUFFIX = /\/(ncover|lite|flash)$/;

export function toOriginalImageUrl(url: string): string {
  return url.replace(THUMBNAIL_SUFFIX, '');
}

/** 需要从 raw_html 中剔除的元素 */
export const STRIP_TAGS = ['script', 'style', 'noscript', 'svg', 'iframe', 'link', 'meta'];

/** raw_html 保留的属性，其余（data-v-*、style、事件属性）全部移除 */
export const KEEP_ATTRIBUTES = new Set(['class', 'src', 'href', 'title', 'alt', 'id']);
