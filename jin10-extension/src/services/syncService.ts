/**
 * 团队同步（在 background 中运行）：
 *   1. 上传服务端缺少的图片
 *   2. 推送本地待同步素材（新增、补充、删除），用服务端返回的最终版本替换本地记录
 *   3. 按游标拉取团队其他成员的变更
 * 同一时刻只运行一次；本地尚未推送的修改优先，不会被拉取覆盖。
 */
import type { NewsMaterial } from '@/types/material';
import type { AuthSession, SyncReport, SyncState } from '@/types/settings';
import { createSerialQueue } from '@/shared/async';
import { AppException } from '@/shared/errors';
import { logger } from '@/shared/logger';
import { nowIso } from '@/shared/time';
import { getImage } from './assetStore';
import { dedupeKeys } from './materialService';
import type { MaterialRepository } from './repository/MaterialRepository';
import { fetchChanges, fromServer, missingAssets, pushMaterials, uploadAsset } from './api/serverApi';
import { getSession, getSyncState, updateSyncState, saveTeamQueue, readTeamQueue } from './settingsStore';
import { inSession, SessionChanged } from './sessionScope';

const PUSH_BATCH = 50;
const SYNCED_RETENTION_MS = 14 * 24 * 60 * 60_000;

function assetIds(materials: NewsMaterial[]): string[] {
  const ids = new Set<string>();
  for (const m of materials) {
    const images = [...m.images, ...(m.interpretation?.modules.flatMap((mod) => mod.images) ?? [])];
    for (const img of images) if (img.asset_id) ids.add(img.asset_id);
  }
  return [...ids];
}

async function uploadMissingAssets(pending: NewsMaterial[], session: AuthSession): Promise<number> {
  await inSession(session,async()=>{});
  const missing = await missingAssets(assetIds(pending), session);
  let uploaded = 0;
  for (const id of missing) {
    const asset = await getImage(id);
    if (!asset) continue; // 本地也没有文件（下载失败），素材里只保留链接
    await inSession(session,async()=>{});
    await uploadAsset(asset, session);
    uploaded++;
  }
  return uploaded;
}

async function push(repo: MaterialRepository, pending: NewsMaterial[], session: AuthSession): Promise<number> {
  for (let i = 0; i < pending.length; i += PUSH_BATCH) {
    await inSession(session,async()=>{});
    const batch = pending.slice(i, i + PUSH_BATCH);
    const results = await pushMaterials(batch, session);
    for (const r of results) {
      const sent = batch.find(m => m.id === r.client_id);
      if (!sent) throw new Error('服务端同步回包包含未发送的素材');
      const m = r.material ? fromServer(r.material) : null;
      await inSession(session,()=>repo.acknowledge(sent, m, dedupeKeys((m || sent).source_item_id, (m || sent).fingerprint)));
    }
  }
  return pending.length;
}

async function pull(repo: MaterialRepository, cursor: string | null, session: AuthSession): Promise<{ pulled: number; cursor: string | null }> {
  let pulled = 0;
  for (;;) {
    await inSession(session,async()=>{});
    const page = await fetchChanges(cursor, 200, session);
    for (const item of page.items) {
      const m = fromServer(item);
      const applied=await inSession(session,()=>repo.applyRemote(m,dedupeKeys(m.source_item_id,m.fingerprint)));
      if(applied) pulled++;
    }
    cursor = page.next_cursor;
    if (!page.has_more) return { pulled, cursor };
  }
}

function mergeUniqueMaterials(primary: NewsMaterial[], secondary: NewsMaterial[]): NewsMaterial[] {
  const result: NewsMaterial[] = [];
  const seenIds = new Set<string>();
  const seenKeys = new Set<string>();
  for (const item of [...primary, ...secondary]) {
    if (seenIds.has(item.id)) continue;
    const keys = dedupeKeys(item.source_item_id, item.fingerprint);
    if (keys.some((k) => seenKeys.has(k))) continue;
    seenIds.add(item.id);
    for (const k of keys) seenKeys.add(k);
    result.push(item);
  }
  return result;
}

/** Caller holds the authentication lock, including before a newly collected local save. */
export async function activateScope(repo: MaterialRepository, session: AuthSession): Promise<SyncState> {
  let state = await getSyncState();
  const nextScope = `${session.api_base_url}:${session.team.id}`;
  const prevScope = state.team_id ? `${state.server_url || session.api_base_url}:${state.team_id}` : null;

  if (!state.team_id) {
    const all = await repo.listAll();
    const offlineItems = all
      .filter((m) => (!m.team_id || m.team_id === session.team.id) && m.sync_status !== 'synced')
      .map((m) => ({ ...m, team_id: session.team.id }));
    const queued = (await readTeamQueue(nextScope)).map((m) => ({ ...m, team_id: session.team.id }));
    const merged = mergeUniqueMaterials(offlineItems, queued);
    await repo.resetAll(merged);
    state = await updateSyncState({ cursor: null, team_id: session.team.id, server_url: session.api_base_url });
    if (queued.length > 0) await saveTeamQueue(nextScope, []);
    return state;
  }

  if (state.team_id !== session.team.id || (state.server_url && state.server_url !== session.api_base_url)) {
    const all = await repo.listAll();
    if (prevScope) {
      // Preserve offline work under its original team; never submit it to the new team.
      const prevUnsynced = all
        .filter((m) => m.sync_status !== 'synced' && (!m.team_id || m.team_id === state.team_id))
        .map((m) => ({ ...m, team_id: state.team_id }));
      const existingPrev = await readTeamQueue(prevScope);
      const mergedPrev = mergeUniqueMaterials(prevUnsynced, existingPrev);
      if (mergedPrev.length > 0) await saveTeamQueue(prevScope, mergedPrev);
    }
    const nextQueued = (await readTeamQueue(nextScope)).map((m) => ({ ...m, team_id: session.team.id }));
    await repo.resetAll(nextQueued);
    state = await updateSyncState({ cursor: null, team_id: session.team.id, server_url: session.api_base_url });
    if (nextQueued.length > 0) await saveTeamQueue(nextScope, []);
    return state;
  }

  if (!state.server_url) {
    state = await updateSyncState({ server_url: session.api_base_url });
  }
  return state;
}

/** Caller holds the authentication lock. Saves current team's unsynced queue and clears active scope. */
export async function deactivateScope(repo: MaterialRepository, fallbackServerUrl?: string): Promise<void> {
  const state = await getSyncState();
  const all = await repo.listAll();
  if (state.team_id) {
    const serverUrl = state.server_url || fallbackServerUrl || '';
    const unsynced = all
      .filter((m) => m.sync_status !== 'synced' && (!m.team_id || m.team_id === state.team_id))
      .map((m) => ({ ...m, team_id: state.team_id }));
    if (serverUrl && unsynced.length > 0) {
      const scope = `${serverUrl}:${state.team_id}`;
      const existing = await readTeamQueue(scope);
      await saveTeamQueue(scope, mergeUniqueMaterials(unsynced, existing));
    }
    await repo.resetAll([]);
  } else if (all.some((m) => m.team_id)) {
    await repo.resetAll(all.filter((m) => !m.team_id && m.sync_status !== 'synced'));
  }
  await updateSyncState({
    team_id: null,
    server_url: undefined,
    cursor: null,
    syncing: false,
    sync_started_at: null,
    last_error: null,
  });
}

export function createSyncService(repo: MaterialRepository) {
  const serial = createSerialQueue();

  async function run(): Promise<SyncReport> {
    const session = await getSession();
    if (!session) return { skipped: 'not_logged_in', pushed: 0, pulled: 0, assets_uploaded: 0 };
    try {
      const state = await inSession(session, async () => {
        const nextState = await activateScope(repo, session);
        await updateSyncState({ syncing: true, sync_started_at: nowIso() });
        return nextState;
      });
      const pending = (await repo.listAll()).filter((m) => m.sync_status !== 'synced' && (!m.team_id || m.team_id === session.team.id));
      const canWrite = session.user.role !== 'viewer';
      const assetsUploaded = canWrite && pending.length ? await uploadMissingAssets(pending, session) : 0;
      const pushed = canWrite && pending.length ? await push(repo, pending, session) : 0;
      const { pulled, cursor } = await pull(repo, state.cursor, session);
      await inSession(session, async () => {
        await repo.pruneSynced?.(SYNCED_RETENTION_MS);
        await updateSyncState({ cursor, last_sync_at: nowIso(), last_error: null, syncing: false, sync_started_at: null });
      });
      return { skipped: null, pushed, pulled, assets_uploaded: assetsUploaded };
    } catch (err) {
      if (err instanceof SessionChanged) {
        if (!(await getSession())) await updateSyncState({ syncing: false, sync_started_at: null });
        return { skipped: 'session_changed', pushed: 0, pulled: 0, assets_uploaded: 0 };
      }
      const message = err instanceof Error ? err.message : String(err);
      logger.warn('sync failed', message);
      try { await inSession(session, () => updateSyncState({ last_error: message, syncing: false, sync_started_at: null })); }
      catch (changed) { if (!(changed instanceof SessionChanged)) throw changed; }
      throw err instanceof AppException ? err : new AppException('NETWORK', message);
    }
  }

  return { sync: () => serial(run) };
}

export type SyncService = ReturnType<typeof createSyncService>;
