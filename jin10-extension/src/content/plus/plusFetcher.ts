import type { InterpretationStatus, PlusInterpretation } from '@/types/material';
import { createSerialQueue, sleep, waitFor } from '@/shared/async';
import { logger } from '@/shared/logger';
import { nowIso } from '@/shared/time';
import { cleanText } from '../parser/normalize';
import { ANNOTATION, SELECTORS } from '../parser/selectors';
import { parseAnnotation, parsePlusId } from './annotationParser';

export type PlusFetchResult =
  | { status: 'fetched'; interpretation: PlusInterpretation }
  | { status: Exclude<InterpretationStatus, 'fetched' | 'pending' | 'manual'>; error: string | null };

const OPEN_TIMEOUT = 5000;
const LOAD_TIMEOUT = 10000;
const CLOSE_TIMEOUT = 1500;

/** 同一时刻只允许一个注解弹窗被操作，避免连续右键时互相干扰 */
const serial = createSerialQueue();

function currentIframe(): HTMLIFrameElement | null {
  return document.querySelector<HTMLIFrameElement>(SELECTORS.plusIframe);
}

/** 金十弹窗在页面 mousedown 时关闭（Esc、click 均无效，2026-09-21 实测） */
async function closePopup(): Promise<void> {
  if (!document.querySelector(SELECTORS.plusPopup)) return;
  document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
  document.body.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true }));
  await waitFor(() => (document.querySelector(SELECTORS.plusPopup) ? null : true), {
    timeout: CLOSE_TIMEOUT,
  });
}

type LoadState =
  | { kind: 'ready'; doc: Document }
  | { kind: 'no_permission' }
  | { kind: 'error'; message: string };

function inspectIframe(iframe: HTMLIFrameElement, expectedTitle: string | null): LoadState | null {
  let doc: Document | null;
  let hash: string;
  try {
    doc = iframe.contentDocument;
    hash = iframe.contentWindow?.location.hash ?? '';
  } catch {
    return { kind: 'error', message: '无法访问注解页面' };
  }
  if (!doc) return null;
  if (hash.startsWith('#/401')) return { kind: 'no_permission' };

  const preview = doc.querySelector(`${ANNOTATION.content} ${ANNOTATION.preview}`);
  if (preview?.querySelector(ANNOTATION.module)) {
    const title = cleanText(preview.querySelector(ANNOTATION.title)?.textContent);
    // 弹窗会复用：标题与按钮一致才说明已切换到本条注解
    if (!expectedTitle || title === expectedTitle) return { kind: 'ready', doc };
    return null;
  }

  const state = doc.querySelector(ANNOTATION.state);
  const heading = cleanText(state?.querySelector('h1')?.textContent);
  if (heading) return { kind: 'error', message: heading };
  return null; // 仍在加载
}

async function fetchOnce(body: Element, expectedTitle: string | null): Promise<PlusFetchResult> {
  const link = body.querySelector<HTMLElement>(SELECTORS.plusLink);
  if (!link) return { status: 'none', error: null };

  const previousSrc = currentIframe()?.src ?? null;
  link.click();

  const iframe = await waitFor(
    () => {
      const el = currentIframe();
      if (!el || !parsePlusId(el.src)) return null;
      // 有期望标题时，后续按标题校验内容，src 相同也可接受（重复点开同一条）
      return el.src !== previousSrc || expectedTitle !== null ? el : null;
    },
    { timeout: OPEN_TIMEOUT },
  );
  if (!iframe) return { status: 'failed', error: '注解弹窗未打开' };

  const state = await waitFor(() => inspectIframe(iframe, expectedTitle), { timeout: LOAD_TIMEOUT });
  if (!state) return { status: 'failed', error: '注解加载超时' };
  if (state.kind === 'no_permission') return { status: 'no_permission', error: '未登录或无 PLUS 会员权限' };
  if (state.kind === 'error') return { status: 'failed', error: state.message };

  const parsed = parseAnnotation(state.doc, iframe.src);
  if (!parsed) return { status: 'failed', error: '注解结构无法识别' };

  return {
    status: 'fetched',
    interpretation: {
      plus_id: parsePlusId(iframe.src),
      title: parsed.title,
      modules: parsed.modules,
      fetched_at: nowIso(),
    },
  };
}

/**
 * 打开该快讯的 PLUS 注解弹窗，读取渲染后的正文，然后关闭弹窗。
 * 走金十自身页面与用户本人的登录态，不直接调用金十接口。
 */
export function fetchPlusInterpretation(
  body: Element,
  expectedTitle: string | null,
): Promise<PlusFetchResult> {
  return serial(async () => {
    try {
      return await fetchOnce(body, expectedTitle);
    } catch (err) {
      logger.warn('plus fetch failed', err);
      return { status: 'failed', error: err instanceof Error ? err.message : String(err) };
    } finally {
      await closePopup();
      await sleep(50);
    }
  });
}
