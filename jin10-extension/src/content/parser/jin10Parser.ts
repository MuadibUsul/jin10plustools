import type { ItemKind, MaterialImage, NewsMaterialDraft } from '@/types/material';
import type { ScanResponse } from '@/types/messages';
import { beijingDateParts, inferYear, toBeijingIso, type DateParts } from '@/shared/time';
import { IGNORED_IMAGE_PATTERNS, SELECTORS, toOriginalImageUrl } from './selectors';
import {
  cleanText,
  firstSentence,
  parseItemIdTime,
  parsePrefixDate,
  sanitizeHtml,
  stripJin10Prefix,
  stripReadMore,
  textWithout,
} from './normalize';

export interface ParseContext {
  pageUrl: string;
  now: Date;
}

export type ParseResult =
  | { ok: true; draft: NewsMaterialDraft }
  | { ok: false; reason: string };

/**
 * 从任意目标元素（右键点击处）向上找到所属快讯。
 * 优先返回包含时间的整条快讯 .jin-flash-item，找不到则退回主体 .item-right。
 */
export function findFlashRoot(target: Element | null): Element | null {
  if (!target) return null;
  return target.closest(SELECTORS.flashItem) ?? target.closest(SELECTORS.body);
}

function getBody(root: Element): Element | null {
  return root.matches(SELECTORS.body) ? root : root.querySelector(SELECTORS.body);
}

function detectKind(root: Element): ItemKind {
  const item = root.closest(SELECTORS.flashItem) ?? root;
  if (item.classList.contains('article')) return 'article';
  if (item.classList.contains('is-vip')) return 'vip';
  return 'flash';
}

function resolveUrl(raw: string | null, base: string): string | null {
  if (!raw) return null;
  try {
    const url = new URL(raw, base).href;
    return /^https?:/.test(url) ? url : null;
  } catch {
    return null;
  }
}

function extractTags(body: Element): string[] {
  const tags = Array.from(body.querySelectorAll(SELECTORS.tag), (el) => cleanText(el.textContent));
  return [...new Set(tags.filter(Boolean))];
}

function extractPlusTitle(body: Element): string | null {
  const el = body.querySelector(SELECTORS.plusTitle);
  if (!el) return null;
  return cleanText(el.getAttribute('title')) || cleanText(el.textContent) || null;
}

function extractImages(body: Element, pageUrl: string): MaterialImage[] {
  const seen = new Set<string>();
  const images: MaterialImage[] = [];
  for (const img of Array.from(body.querySelectorAll('img'))) {
    if (img.closest(SELECTORS.remark)) continue; // PLUS 按钮里的会员图标
    if (img.closest(SELECTORS.miniprogram)) continue; // 小程序卡片（「扫码查看」）的封面缩略图，不是快讯配图
    const url = resolveUrl(img.getAttribute('data-src') || img.getAttribute('src'), pageUrl);
    if (!url || IGNORED_IMAGE_PATTERNS.some((p) => p.test(url))) continue;
    const original = toOriginalImageUrl(url);
    if (seen.has(original)) continue;
    seen.add(original);
    images.push({ url: original, source: 'flash', asset_id: null });
  }
  return images;
}

/** 发布时间优先级：条目 ID > 正文日期前缀 + 条目时间 > 今日 + 条目时间 */
function extractPublishTime(
  root: Element,
  itemId: string | null,
  contentRaw: string,
  now: Date,
): string | null {
  const fromId = parseItemIdTime(itemId);
  if (fromId) return fromId;

  const time = cleanText(root.querySelector(SELECTORS.time)?.textContent);
  if (!time) return null;
  const prefix = parsePrefixDate(contentRaw);
  const date: DateParts = prefix
    ? { year: inferYear(prefix.month, prefix.day, now), ...prefix }
    : beijingDateParts(now);
  return toBeijingIso(date, time);
}

interface ExtractedText {
  flashTitle: string | null;
  contentRaw: string;
  content: string;
  detailUrl: string | null;
}

function extractFlashText(body: Element): ExtractedText {
  const contentRaw = textWithout(body.querySelector(SELECTORS.content), SELECTORS.tag);
  return {
    flashTitle: textWithout(body.querySelector(SELECTORS.title), SELECTORS.tag) || null,
    contentRaw,
    content: stripJin10Prefix(contentRaw),
    detailUrl: null,
  };
}

function extractArticleText(body: Element, pageUrl: string): ExtractedText {
  const intro = cleanText(body.querySelector(SELECTORS.articleIntro)?.textContent);
  return {
    flashTitle: textWithout(body.querySelector(SELECTORS.articleTitle), SELECTORS.tag) || null,
    contentRaw: intro,
    content: stripReadMore(intro),
    detailUrl: resolveUrl(
      body.querySelector(SELECTORS.remarkLink)?.getAttribute('href') ?? null,
      pageUrl,
    ),
  };
}

export function parseFlashItem(root: Element, ctx: ParseContext): ParseResult {
  const body = getBody(root);
  if (!body) return { ok: false, reason: '未找到快讯主体 .item-right' };

  const kind = detectKind(root);
  const locked = body.matches(SELECTORS.locked);
  const text = kind === 'article' ? extractArticleText(body, ctx.pageUrl) : extractFlashText(body);

  if (!text.content && !text.flashTitle) return { ok: false, reason: '快讯正文为空' };

  const itemId = root.closest(SELECTORS.itemContainer)?.id ?? null;
  const plusTitle = extractPlusTitle(body);

  const draft: NewsMaterialDraft = {
    source: 'jin10',
    source_item_id: itemId,
    item_kind: kind,
    locked,
    tags: extractTags(body),
    detail_url: text.detailUrl,
    title: plusTitle ?? text.flashTitle ?? firstSentence(text.content),
    flash_title: text.flashTitle,
    content: text.content,
    content_raw: text.contentRaw,
    plus_title: plusTitle,
    has_plus_link: body.querySelector(SELECTORS.plusLink) !== null,
    images: extractImages(body, ctx.pageUrl),
    publish_time: extractPublishTime(root, itemId, text.contentRaw, ctx.now),
    url: ctx.pageUrl,
    raw_html: sanitizeHtml(root),
  };
  return { ok: true, draft };
}

/** 批量解析：扫描容器内所有快讯（只包含当前已加载到页面的条目） */
export function scanFlashItems(scope: ParentNode, ctx: ParseContext): ScanResponse {
  let roots = Array.from(scope.querySelectorAll(SELECTORS.flashItem));
  if (roots.length === 0) roots = Array.from(scope.querySelectorAll(SELECTORS.body));

  const drafts: NewsMaterialDraft[] = [];
  const failures: ScanResponse['failures'] = [];
  roots.forEach((root, index) => {
    const result = parseFlashItem(root, ctx);
    if (result.ok) drafts.push(result.draft);
    else failures.push({ index, reason: result.reason });
  });
  return { total: roots.length, drafts, failures };
}
