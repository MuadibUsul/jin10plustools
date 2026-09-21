/**
 * 团队同步（在 background 中运行）：
 *   1. 上传服务端缺少的图片
 *   2. 推送本地待同步素材（新增、补充、删除），用服务端返回的最终版本替换本地记录
 *   3. 按游标拉取团队其他成员的变更
 * 同一时刻只运行一次；本地尚未推送的修改优先，不会被拉取覆盖。
 */
import type { NewsMaterial } from '@/types/material';
import type { SyncReport } from '@/types/settings';
import { createSerialQueue } from '@/shared/async';
import { AppException } from '@/shared/errors';
import { logger } from '@/shared/logger';
import { nowIso } from '@/shared/time';
import { getImage } from './assetStore';
import { dedupeKeys } from './materialService';
import type { MaterialRepository } from './repository/MaterialRepository';
import { fetchChanges, fromServer, missingAssets, pushMaterials, uploadAsset } from './api/serverApi';
import { getSession, getSyncState, updateSyncState } from './settingsStore';

const PUSH_BATCH = 50;

function assetIds(materials: NewsMaterial[]): string[] {
  const ids = new Set<string>();
  for (const m of materials) {
    const images = [...m.images, ...(m.interpretation?.modules.flatMap((mod) => mod.images) ?? [])];
    for (const img of images) if (img.asset_id) ids.add(img.asset_id);
  }
  return [...ids];
}

async function uploadMissingAssets(pending: NewsMaterial[]): Promise<number> {
  const missing = await missingAssets(assetIds(pending));
  let uploaded = 0;
  for (const id of missing) {
    const asset = await getImage(id);
    if (!asset) continue; // 本地也没有文件（下载失败），素材里只保留链接
    await uploadAsset(asset);
    uploaded++;
  }
  return uploaded;
}

async function push(repo: MaterialRepository, pending: NewsMaterial[]): Promise<number> {
  for (let i = 0; i < pending.length; i += PUSH_BATCH) {
    const batch = pending.slice(i, i + PUSH_BATCH);
    const results = await pushMaterials(batch);
    for (const r of results) {
      if (r.material) {
        const m = fromServer(r.material);
        await repo.replace(r.client_id, m, dedupeKeys(m.source_item_id, m.fingerprint));
      } else {
        await repo.remove(r.client_id); // 本地删除了从未上传过的素材
      }
    }
  }
  return pending.length;
}

async function pull(repo: MaterialRepository, cursor: string | null): Promise<{ pulled: number; cursor: string | null }> {
  let pulled = 0;
  for (;;) {
    const page = await fetchChanges(cursor);
    for (const item of page.items) {
      const local = await repo.get(item.id);
      if (local?.sync_status === 'local') continue; // 本地有未推送的修改，下次推送时由服务端合并
      const m = fromServer(item);
      await repo.save(m, dedupeKeys(m.source_item_id, m.fingerprint));
      pulled++;
    }
    cursor = page.next_cursor;
    if (!page.has_more) return { pulled, cursor };
  }
}

export function createSyncService(repo: MaterialRepository) {
  const serial = createSerialQueue();

  async function run(): Promise<SyncReport> {
    const session = await getSession();
    if (!session) return { skipped: 'not_logged_in', pushed: 0, pulled: 0, assets_uploaded: 0 };

    let state = await getSyncState();
    if (state.team_id !== session.team.id) {
      // 切换了团队：上一个团队同步下来的素材不属于当前团队，清除后从头拉取
      for (const m of await repo.listAll()) if (m.sync_status === 'synced') await repo.remove(m.id);
      state = await updateSyncState({ cursor: null, team_id: session.team.id });
    }

    await updateSyncState({ syncing: true });
    try {
      const pending = (await repo.listAll()).filter((m) => m.sync_status !== 'synced');
      const canWrite = session.user.role !== 'viewer';
      const assetsUploaded = canWrite && pending.length ? await uploadMissingAssets(pending) : 0;
      const pushed = canWrite && pending.length ? await push(repo, pending) : 0;
      const { pulled, cursor } = await pull(repo, state.cursor);
      await updateSyncState({ cursor, last_sync_at: nowIso(), last_error: null, syncing: false });
      return { skipped: null, pushed, pulled, assets_uploaded: assetsUploaded };
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      logger.warn('sync failed', message);
      await updateSyncState({ last_error: message, syncing: false });
      throw err instanceof AppException ? err : new AppException('NETWORK', message);
    }
  }

  return { sync: () => serial(run) };
}

export type SyncService = ReturnType<typeof createSyncService>;
