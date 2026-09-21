import type { MaterialImage, PlusModule, PlusModuleKind } from '@/types/material';
import { cleanText } from '../parser/normalize';
import { ANNOTATION, IGNORED_IMAGE_PATTERNS, toOriginalImageUrl } from '../parser/selectors';

export interface ParsedAnnotation {
  title: string;
  modules: PlusModule[];
}

/** 按换行拆分段落（注解正文用 white-space: pre-line 渲染换行） */
function splitParagraphs(raw: string | null | undefined): string[] {
  if (!raw) return [];
  return raw.split(/\n+/).map(cleanText).filter(Boolean);
}

function detectKind(module: Element): PlusModuleKind {
  if (module.querySelector(ANNOTATION.observationItems)) return 'observation';
  if (module.querySelector(ANNOTATION.viewText)) return 'view';
  return 'unknown';
}

function extractParagraphs(module: Element, kind: PlusModuleKind): string[] {
  switch (kind) {
    case 'observation':
      return Array.from(module.querySelectorAll(ANNOTATION.observationText)).flatMap((el) =>
        splitParagraphs(el.textContent),
      );
    case 'view':
      return Array.from(module.querySelectorAll(ANNOTATION.viewText)).flatMap((el) =>
        splitParagraphs(el.textContent),
      );
    case 'unknown': {
      // 未知模块：取所有段落，拿不到则退回整块文本（去掉标题）
      const heading = module.querySelector(ANNOTATION.heading)?.textContent ?? '';
      const ps = Array.from(module.querySelectorAll(ANNOTATION.paragraph)).flatMap((el) =>
        splitParagraphs(el.textContent),
      );
      if (ps.length > 0) return ps;
      return splitParagraphs(module.textContent?.replace(heading, ''));
    }
  }
}

function extractImages(module: Element, baseUrl: string): MaterialImage[] {
  const seen = new Set<string>();
  const images: MaterialImage[] = [];
  for (const img of Array.from(module.querySelectorAll('img'))) {
    const raw = img.getAttribute('src') || img.getAttribute('data-src');
    if (!raw) continue;
    let url: string;
    try {
      url = toOriginalImageUrl(new URL(raw, baseUrl).href);
    } catch {
      continue;
    }
    if (!/^https?:/.test(url) || IGNORED_IMAGE_PATTERNS.some((p) => p.test(url))) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    images.push({ url, source: 'plus', asset_id: null });
  }
  return images;
}

/** 解析渲染完成的注解正文；结构不符（未加载完 / 协议变更）返回 null */
export function parseAnnotation(scope: ParentNode, baseUrl: string): ParsedAnnotation | null {
  const preview = scope.querySelector(ANNOTATION.preview);
  if (!preview) return null;

  const title = cleanText(preview.querySelector(ANNOTATION.title)?.textContent);
  const modules = Array.from(preview.querySelectorAll(ANNOTATION.module))
    .map((module): PlusModule => {
      const kind = detectKind(module);
      return {
        kind,
        heading: cleanText(module.querySelector(ANNOTATION.heading)?.textContent) || null,
        paragraphs: extractParagraphs(module, kind),
        images: extractImages(module, baseUrl),
      };
    })
    .filter((m) => m.paragraphs.length > 0 || m.images.length > 0);

  if (!title || modules.length === 0) return null;
  return { title, modules };
}

/** 从 iframe 地址中取注解 ID：…/plus/index.html#/?id=6618 */
export function parsePlusId(src: string | null | undefined): string | null {
  if (!src) return null;
  const m = /[?&]id=(\d+)/.exec(src);
  return m?.[1] ?? null;
}
