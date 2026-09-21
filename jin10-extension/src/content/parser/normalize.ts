import { MAX_RAW_HTML_LENGTH } from '@/shared/constants';
import { KEEP_ATTRIBUTES, STRIP_TAGS } from './selectors';

/** 合并空白、去掉不间断空格与首尾空白 */
export function cleanText(input: string | null | undefined): string {
  if (!input) return '';
  return input
    .replace(/[\u00a0\u3000]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** 元素文本，排除指定子元素（如内嵌的 .jin-tag 标签） */
export function textWithout(el: Element | null, excludeSelector: string): string {
  if (!el) return '';
  const clone = el.cloneNode(true) as Element;
  clone.querySelectorAll(excludeSelector).forEach((c) => c.remove());
  return cleanText(clone.textContent);
}

/** 文章摘要末尾的「点击查看...」 */
export function stripReadMore(text: string): string {
  return text.replace(/[….。]*\s*点击查看[….。]*$/, '').trim();
}

const ITEM_ID_PATTERN = /^flash(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})/;

/** 从条目 ID 解析发布时间（北京时间），格式不符返回 null */
export function parseItemIdTime(id: string | null): string | null {
  const m = id ? ITEM_ID_PATTERN.exec(id) : null;
  if (!m) return null;
  const [, y, mo, d, h, mi, s] = m;
  return `${y}-${mo}-${d}T${h}:${mi}:${s}+08:00`;
}

const PREFIX_PATTERN = /^金十数据\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日讯\s*[，,:：]?\s*/;

/** 去掉快讯正文开头的「金十数据9月21日讯，」 */
export function stripJin10Prefix(text: string): string {
  return text.replace(PREFIX_PATTERN, '');
}

/** 从正文前缀解析月/日，没有前缀返回 null */
export function parsePrefixDate(text: string): { month: number; day: number } | null {
  const m = PREFIX_PATTERN.exec(text);
  if (!m) return null;
  const month = Number(m[1]);
  const day = Number(m[2]);
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  return { month, day };
}

/** 正文首句，作为无标题快讯的兜底标题 */
export function firstSentence(text: string, maxLength = 40): string {
  const sentence = text.split(/(?<=[。！？!?；;])/)[0] ?? text;
  const trimmed = sentence.replace(/[。！？!?；;]$/, '').trim();
  return trimmed.length > maxLength ? `${trimmed.slice(0, maxLength)}…` : trimmed;
}

/**
 * 生成可安全存储的组件 HTML：
 * 删除脚本、样式、SVG、iframe 与注释，只保留少量语义属性。
 * 该 HTML 仅用于存档与重新解析，界面上不会以 innerHTML 渲染。
 */
export function sanitizeHtml(root: Element): string {
  const clone = root.cloneNode(true) as Element;

  clone.querySelectorAll(STRIP_TAGS.join(',')).forEach((el) => el.remove());

  const doc = root.ownerDocument;
  const walker = doc.createTreeWalker(clone, NodeFilter.SHOW_COMMENT);
  const comments: Node[] = [];
  while (walker.nextNode()) comments.push(walker.currentNode);
  comments.forEach((c) => c.parentNode?.removeChild(c));

  for (const el of [clone, ...Array.from(clone.querySelectorAll('*'))]) {
    for (const attr of Array.from(el.attributes)) {
      if (!KEEP_ATTRIBUTES.has(attr.name)) el.removeAttribute(attr.name);
    }
    const href = el.getAttribute('href');
    if (href && /^\s*javascript:/i.test(href)) el.removeAttribute('href');
  }

  const html = clone.outerHTML;
  return html.length > MAX_RAW_HTML_LENGTH ? html.slice(0, MAX_RAW_HTML_LENGTH) : html;
}
