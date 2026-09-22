import type { InterpretationStatus, MaterialImage, PlusInterpretation } from '@/types/material';
import type { CollectResponse, SaveMaterialResponse } from '@/types/messages';
import { AppException } from '@/shared/errors';
import { sendToBackground } from '@/shared/messaging';
import { getLastContextTarget } from './contextTracker';
import { downloadImages } from './imageDownloader';
import { findFlashRoot, parseFlashItem } from './parser/jin10Parser';
import { SELECTORS } from './parser/selectors';
import { fetchPlusInterpretation } from './plus/plusFetcher';
import { showToast, type ToastHandle } from './toast';
import type { ParseReport } from '@/types/messages';

/** 上报异常（不影响采集流程），集中出现说明金十页面结构变了 */
function report(kind: ParseReport['kind'], root: Element, sourceItemId: string | null, detail: string): void {
  void sendToBackground('REPORT_PARSE', { kind, source_item_id: sourceItemId, detail, html: root.outerHTML.slice(0, 30_000) });
}

const TITLE_LIKE = 'b, strong, h1, h2, h3, h4, [class*="title"]';

/** 快讯里有像标题的加粗文字，但解析器没认出来（新样式的信号） */
function unrecognizedTitle(root: Element, known: (string | null)[]): string | null {
  for (const el of Array.from(root.querySelectorAll(TITLE_LIKE))) {
    if (el.closest(`${SELECTORS.content}, ${SELECTORS.remark}, .item-time`)) continue;
    const text = el.textContent?.replace(/\s+/g, ' ').trim() ?? '';
    if (text.length >= 6 && text.length <= 80 && !known.some((k) => k?.includes(text))) return text;
  }
  return null;
}

/** 插件有新版本时提醒（每次采集后检查，结果在后台缓存） */
async function remindUpdate(): Promise<void> {
  const res = await sendToBackground('CHECK_UPDATE', undefined);
  if (res.ok && res.data.outdated) {
    showToast('warning', `插件有新版本 v${res.data.latest}`, '请到选稿网页下载新版并在扩展管理页刷新').dismiss(8000);
  }
}

function allImages(
  flashImages: MaterialImage[],
  interpretation: PlusInterpretation | null,
): MaterialImage[] {
  const plusImages = interpretation?.modules.flatMap((m) => m.images) ?? [];
  const seen = new Set<string>();
  return [...flashImages, ...plusImages].filter((img) => !seen.has(img.url) && seen.add(img.url));
}

const OUTCOME_TEXT: Record<SaveMaterialResponse['outcome'], string> = {
  created: '已加入素材库',
  updated: '素材库已有，已补充新内容',
  duplicate: '素材库已有这条快讯',
};

const INTERPRETATION_TEXT: Partial<Record<InterpretationStatus, string>> = {
  fetched: '含 PLUS 解读',
  no_permission: 'PLUS 解读需要登录会员账号',
  failed: 'PLUS 解读读取失败，可稍后重试',
};

function describe(res: CollectResponse): string {
  const parts: string[] = [];
  const interp = INTERPRETATION_TEXT[res.interpretation_status];
  if (interp) parts.push(interp);
  if (res.images_total > 0) {
    parts.push(
      res.images_saved === res.images_total
        ? `配图 ${res.images_saved} 张`
        : `配图 ${res.images_saved}/${res.images_total} 张（部分下载失败）`,
    );
  }
  return parts.join(' · ');
}

/** 采集最近一次右键位置所在的快讯 */
export async function collectAtContext(analyze: boolean): Promise<CollectResponse> {
  const root = findFlashRoot(getLastContextTarget());
  if (!root) {
    showToast('warning', '未识别到新闻组件', '请在快讯内容区域右键').dismiss();
    throw new AppException('NOT_FOUND', '未识别到新闻组件');
  }
  return collectFlash(root, analyze);
}

/** 采集一条快讯：解析 → 读取 PLUS 解读 → 下载配图 → 交给 background 入库 */
export async function collectFlash(root: Element, analyze: boolean): Promise<CollectResponse> {
  const parsed = parseFlashItem(root, { pageUrl: location.href, now: new Date() });
  if (!parsed.ok) {
    showToast('error', '解析失败', parsed.reason).dismiss();
    report('parse_failed', root, null, parsed.reason);
    throw new AppException('PARSE_FAILED', parsed.reason);
  }
  const { draft } = parsed;

  const toast: ToastHandle = showToast('info', '正在采集…', draft.title);
  try {
    let interpretation: PlusInterpretation | null = null;
    let status: InterpretationStatus = 'none';
    let interpretationError: string | null = null;

    const body = root.matches(SELECTORS.body) ? root : root.querySelector(SELECTORS.body);
    if (draft.has_plus_link && body) {
      toast.update('info', '正在读取 PLUS 解读…', draft.title);
      const result = await fetchPlusInterpretation(body, draft.plus_title);
      if (result.status === 'fetched') {
        interpretation = result.interpretation;
        status = 'fetched';
      } else {
        status = result.status;
        interpretationError = result.error;
      }
    }

    const imageRefs = allImages(draft.images, interpretation);
    if (imageRefs.length > 0) toast.update('info', `正在下载配图（${imageRefs.length} 张）…`, draft.title);
    const images = await downloadImages(imageRefs);

    const saved = await sendToBackground('SAVE_MATERIAL', {
      draft,
      interpretation,
      interpretation_status: status,
      interpretation_error: interpretationError,
      images,
      method: 'context_menu',
      analyze,
    });
    if (!saved.ok) throw new AppException(saved.error.code, saved.error.message);

    const response: CollectResponse = {
      save: saved.data,
      interpretation_status: status,
      interpretation_error: interpretationError,
      images_saved: images.length,
      images_total: imageRefs.length,
    };
    const tone = saved.data.outcome === 'duplicate' ? 'info' : status === 'fetched' || status === 'none' ? 'success' : 'warning';
    toast.update(tone, OUTCOME_TEXT[saved.data.outcome], describe(response) || draft.title);
    toast.dismiss();

    const missedTitle = unrecognizedTitle(root, [draft.title, draft.flash_title, draft.plus_title, draft.content]);
    if (missedTitle) report('unrecognized_title', root, draft.source_item_id, missedTitle);
    if (images.length < imageRefs.length) {
      const failed = imageRefs.filter((r) => !images.some((i) => i.url === r.url)).map((r) => r.url);
      report('image_failed', root, draft.source_item_id, failed.join(' '));
    }
    if (status === 'failed') report('plus_failed', root, draft.source_item_id, interpretationError ?? '');
    void remindUpdate();
    return response;
  } catch (err) {
    toast.update('error', '采集失败', err instanceof Error ? err.message : String(err));
    toast.dismiss(4000);
    throw err;
  }
}
