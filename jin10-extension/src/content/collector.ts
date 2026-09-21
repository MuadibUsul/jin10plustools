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
    return response;
  } catch (err) {
    toast.update('error', '采集失败', err instanceof Error ? err.message : String(err));
    toast.dismiss(4000);
    throw err;
  }
}
