import type { MaterialImage, NewsMaterial, PlusInterpretation } from '@/types/material';
import type { ImageUpload, SaveMaterialRequest, SaveMaterialResponse } from '@/types/messages';
import { SCHEMA_VERSION } from '@/shared/constants';
import { materialFingerprint } from '@/shared/hash';
import { newId } from '@/shared/id';
import { nowIso } from '@/shared/time';
import type { MaterialRepository } from './repository/MaterialRepository';

export interface MaterialServiceDeps {
  repository: MaterialRepository;
  /** 存储图片并返回 asset_id */
  putImage: (upload: ImageUpload) => Promise<string>;
  now?: () => string;
}

export function dedupeKeys(sourceItemId: string | null, fingerprint: string): string[] {
  return [sourceItemId ? `item:${sourceItemId}` : null, `fp:${fingerprint}`].filter(
    (k): k is string => k !== null,
  );
}

function attachAssets(images: MaterialImage[], assets: Map<string, string>): MaterialImage[] {
  return images.map((img) => ({ ...img, asset_id: assets.get(img.url) ?? img.asset_id }));
}

function attachInterpretationAssets(
  interpretation: PlusInterpretation | null,
  assets: Map<string, string>,
): PlusInterpretation | null {
  if (!interpretation) return null;
  return {
    ...interpretation,
    modules: interpretation.modules.map((m) => ({ ...m, images: attachAssets(m.images, assets) })),
  };
}

/** 合并图片列表：按 URL 去重，已有条目缺少 asset_id 时用新的补上 */
function mergeImages(existing: MaterialImage[], incoming: MaterialImage[]): MaterialImage[] {
  const byUrl = new Map(existing.map((img) => [img.url, img]));
  for (const img of incoming) {
    const prev = byUrl.get(img.url);
    if (!prev) byUrl.set(img.url, img);
    else if (!prev.asset_id && img.asset_id) byUrl.set(img.url, { ...prev, asset_id: img.asset_id });
  }
  return [...byUrl.values()];
}

function sameImages(a: MaterialImage[], b: MaterialImage[]): boolean {
  return a.length === b.length && a.every((img, i) => img.url === b[i]?.url && img.asset_id === b[i]?.asset_id);
}

/**
 * 已存在素材时的合并规则（返回 null 表示没有新内容）：
 * - 解读：已有解读（fetched / manual）不覆盖；否则采用本次结果
 * - 图片：追加新图，补齐缺失的本地文件
 * - analyze_requested：只会由 false 变为 true
 */
export function mergeMaterial(
  existing: NewsMaterial,
  incoming: NewsMaterial,
  now: string,
): NewsMaterial | null {
  const next: NewsMaterial = { ...existing };
  let changed = false;

  const hasFinalInterpretation =
    existing.interpretation_status === 'fetched' || existing.interpretation_status === 'manual';
  if (!hasFinalInterpretation && incoming.interpretation_status !== existing.interpretation_status) {
    next.interpretation_status = incoming.interpretation_status;
    next.interpretation = incoming.interpretation;
    changed = true;
  }

  const images = mergeImages(existing.images, incoming.images);
  if (!sameImages(images, existing.images)) {
    next.images = images;
    changed = true;
  }

  if (next.interpretation && incoming.interpretation && hasFinalInterpretation) {
    // 解读正文保持不变，但允许补齐其配图的本地文件
    const assets = new Map(
      incoming.interpretation.modules.flatMap((m) => m.images).map((img) => [img.url, img.asset_id]),
    );
    const modules = next.interpretation.modules.map((m) => ({
      ...m,
      images: m.images.map((img) => ({ ...img, asset_id: img.asset_id ?? assets.get(img.url) ?? null })),
    }));
    if (JSON.stringify(modules) !== JSON.stringify(next.interpretation.modules)) {
      next.interpretation = { ...next.interpretation, modules };
      changed = true;
    }
  }

  if (incoming.analyze_requested && !existing.analyze_requested) {
    next.analyze_requested = true;
    changed = true;
  }

  if (!changed) return null;
  return { ...next, updated_at: now, sync_status: 'local' };
}

export function createMaterialService({ repository, putImage, now = nowIso }: MaterialServiceDeps) {
  async function storeImages(uploads: ImageUpload[]): Promise<Map<string, string>> {
    const assets = new Map<string, string>();
    for (const upload of uploads) assets.set(upload.url, await putImage(upload));
    return assets;
  }

  async function save(req: SaveMaterialRequest): Promise<SaveMaterialResponse> {
    const timestamp = now();
    const fingerprint = await materialFingerprint(req.draft.flash_title, req.draft.content);
    const keys = dedupeKeys(req.draft.source_item_id, fingerprint);
    const assets = await storeImages(req.images);

    const incoming: NewsMaterial = {
      ...req.draft,
      images: attachAssets(req.draft.images, assets),
      id: newId(),
      collect_time: timestamp,
      collect_method: req.method,
      fingerprint,
      interpretation_status: req.interpretation_status,
      interpretation: attachInterpretationAssets(req.interpretation, assets),
      analyze_requested: req.analyze,
      category: null,
      importance_score: 0,
      final_score: 0,
      level: null,
      ai_status: 'pending',
      ai_reason: null,
      ai_error: null,
      collector_id: null,
      collected_by: [],
      team_id: null,
      sync_status: 'local',
      updated_at: timestamp,
      deleted_at: null,
      schema_version: SCHEMA_VERSION,
    };

    const existing = await repository.findByDedupeKeys(keys);

    if (!existing || existing.deleted_at) {
      // 新素材；若曾被删除则沿用原 ID 恢复，便于服务端对齐
      const material = existing ? { ...incoming, id: existing.id } : incoming;
      await repository.save(material, keys);
      return { outcome: 'created', material };
    }

    const merged = mergeMaterial(existing, incoming, timestamp);
    if (!merged) return { outcome: 'duplicate', material: existing };
    await repository.save(merged, keys);
    return { outcome: 'updated', material: merged };
  }

  return {
    save,
    delete: (id: string) => repository.softDelete(id, now()),
    list: () => repository.list(),
  };
}

export type MaterialService = ReturnType<typeof createMaterialService>;
