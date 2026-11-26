import type { NewsMaterial } from '@/types/material';
import { createSerialQueue } from '@/shared/async';
import { STORAGE_KEYS, STORAGE_PREFIX } from '@/shared/constants';
import { AppException } from '@/shared/errors';
import { dedupeKeys } from '../materialService';
import type { MaterialRepository } from './MaterialRepository';
import { acknowledgedMaterial } from './syncAcknowledgement';
import { getSession, getSyncState } from '../settingsStore';

type DedupeIndex = Record<string, string>;

function collectAssetIds(materials: NewsMaterial[], target: Set<string>): void {
  for (const m of materials) {
    for (const img of m.images ?? []) if (img.asset_id) target.add(img.asset_id);
    for (const mod of m.interpretation?.modules ?? []) {
      for (const img of mod.images ?? []) if (img.asset_id) target.add(img.asset_id);
    }
  }
}

/** 新记录插到列表头部（本地采集总是最新的；同步下来的旧素材追加在后面） */
function insertByCollectTime(ids: string[], material: NewsMaterial): string[] {
  return material.sync_status === 'synced' ? [...ids, material.id] : [material.id, ...ids];
}

async function read<T>(key: string, fallback: T): Promise<T> {
  const result = await chrome.storage.local.get(key);
  return (result[key] as T | undefined) ?? fallback;
}

/**
 * chrome.storage.local 实现。
 * 每条素材单独一个键，避免每次读写整个数组；写操作串行执行，防止并发覆盖。
 * 写操作只应在 background 中调用，其他页面只读。
 */
export class LocalMaterialRepository implements MaterialRepository {
  private readonly serial = createSerialQueue();

  async listAll(): Promise<NewsMaterial[]> {
    const ids = await read<string[]>(STORAGE_KEYS.materialIndex, []);
    if (ids.length === 0) return [];
    const keys = ids.map(STORAGE_KEYS.material);
    const records = await chrome.storage.local.get(keys);
    return keys.map((k) => records[k] as NewsMaterial | undefined).filter((m): m is NewsMaterial => m !== undefined);
  }

  async list(): Promise<NewsMaterial[]> {
    const session = await getSession();
    if (!session) {
      const sync = await getSyncState();
      if (sync.team_id) return [];
      return (await this.listAll()).filter((m) => m.deleted_at === null && !m.team_id);
    }
    const state = await getSyncState();
    if (state.team_id !== session.team.id || (state.server_url && state.server_url !== session.api_base_url)) return [];
    return (await this.listAll()).filter((m) => m.deleted_at === null && (!m.team_id || m.team_id === session.team.id));
  }

  get(id: string): Promise<NewsMaterial | null> {
    return read<NewsMaterial | null>(STORAGE_KEYS.material(id), null);
  }

  async findByDedupeKeys(keys: string[]): Promise<NewsMaterial | null> {
    const index = await read<DedupeIndex>(STORAGE_KEYS.materialDedupe, {});
    for (const key of keys) {
      const id = index[key];
      if (id) {
        const found = await this.get(id);
        if (found) return found;
      }
    }
    return null;
  }

  save(material: NewsMaterial, dedupeKeys: string[]): Promise<void> {
    return this.serial(() => this.write(null, material, dedupeKeys));
  }

  replace(oldId: string, material: NewsMaterial, dedupeKeys: string[]): Promise<void> {
    return this.serial(() => this.write(oldId, material, dedupeKeys));
  }

  acknowledge(sent: NewsMaterial, received: NewsMaterial | null, dedupeKeys: string[]): Promise<void> {
    return this.serial(async () => {
      const current = await this.get(sent.id);
      const next = acknowledgedMaterial(sent, current, received);
      if (next) await this.write(sent.id, next, dedupeKeys);
      else if (current) {
        const [ids, index] = await this.readIndexes();
        await chrome.storage.local.set({ [STORAGE_KEYS.materialIndex]: ids.filter(id => id !== sent.id),
          [STORAGE_KEYS.materialDedupe]: Object.fromEntries(Object.entries(index).filter(([,id]) => id !== sent.id)) });
        await chrome.storage.local.remove(STORAGE_KEYS.material(sent.id));
      }
    });
  }

  applyRemote(material: NewsMaterial, keys: string[]): Promise<boolean> {
    return this.serial(async () => {
      const current = await this.get(material.id);
      const byKey = keys.length ? await this.findByDedupeKeys(keys) : null;
      if (current?.sync_status === 'local' || byKey?.sync_status === 'local') return false;
      const oldId = byKey && byKey.id !== material.id ? byKey.id : null;
      await this.write(oldId, material, keys);
      return true;
    });
  }

  resetAll(materials: NewsMaterial[]): Promise<void> {
    return this.serial(async () => {
      try {
        const prevIds = await read<string[]>(STORAGE_KEYS.materialIndex, []);
        const nextIds: string[] = [];
        const seen = new Set<string>();
        const nextDedupe: DedupeIndex = {};
        const payload: Record<string, unknown> = {};
        for (const m of materials) {
          if (seen.has(m.id)) continue;
          seen.add(m.id);
          nextIds.push(m.id);
          payload[STORAGE_KEYS.material(m.id)] = m;
          for (const key of dedupeKeys(m.source_item_id, m.fingerprint)) nextDedupe[key] = m.id;
        }
        payload[STORAGE_KEYS.materialIndex] = nextIds;
        payload[STORAGE_KEYS.materialDedupe] = nextDedupe;
        await chrome.storage.local.set(payload);
        const toRemove = prevIds.filter((id) => !seen.has(id)).map(STORAGE_KEYS.material);
        if (toRemove.length === 1) await chrome.storage.local.remove(toRemove[0]!);
        else if (toRemove.length > 1) await chrome.storage.local.remove(toRemove);
      } catch (err) {
        throw new AppException('STORAGE', `重置失败：${err instanceof Error ? err.message : String(err)}`);
      }
    });
  }

  pruneSynced(maxAgeMs: number, nowMs = Date.now()): Promise<void> {
    return this.serial(async () => {
      const all = await this.listAll();
      const removeIds = new Set<string>();
      const remaining: NewsMaterial[] = [];
      for (const m of all) {
        if (m.sync_status === 'synced') {
          const latest = Math.max(
            Date.parse(m.collect_time) || 0,
            Date.parse(m.updated_at) || 0,
            m.deleted_at ? Date.parse(m.deleted_at) || 0 : 0,
          );
          if (latest > 0 && nowMs - latest > maxAgeMs) {
            removeIds.add(m.id);
            continue;
          }
        }
        remaining.push(m);
      }
      if (removeIds.size > 0) {
        const [ids, index] = await this.readIndexes();
        await chrome.storage.local.set({
          [STORAGE_KEYS.materialIndex]: ids.filter((id) => !removeIds.has(id)),
          [STORAGE_KEYS.materialDedupe]: Object.fromEntries(Object.entries(index).filter(([, id]) => !removeIds.has(id))),
        });
        const keys = [...removeIds].map(STORAGE_KEYS.material);
        await chrome.storage.local.remove(keys.length === 1 ? keys[0]! : keys);
      }
      const dump = (await chrome.storage.local.get(null)) ?? {};
      const referencedAssets = new Set<string>();
      collectAssetIds(
        remaining.filter((m) => m.sync_status !== 'synced'),
        referencedAssets,
      );
      for (const [key, value] of Object.entries(dump)) {
        if (key.startsWith('plus.teamQueue.') && Array.isArray(value)) {
          collectAssetIds(value as NewsMaterial[], referencedAssets);
        }
      }
      const orphanAssetKeys = Object.keys(dump).filter(
        (key) => key.startsWith(STORAGE_PREFIX.asset) && !referencedAssets.has(key.slice(STORAGE_PREFIX.asset.length)),
      );
      if (orphanAssetKeys.length === 1) await chrome.storage.local.remove(orphanAssetKeys[0]!);
      else if (orphanAssetKeys.length > 1) await chrome.storage.local.remove(orphanAssetKeys);
    });
  }

  remove(id: string): Promise<void> {
    return this.serial(async () => {
      const [ids, index] = await this.readIndexes();
      const nextIndex = Object.fromEntries(Object.entries(index).filter(([, v]) => v !== id));
      await chrome.storage.local.set({
        [STORAGE_KEYS.materialIndex]: ids.filter((x) => x !== id),
        [STORAGE_KEYS.materialDedupe]: nextIndex,
      });
      await chrome.storage.local.remove(STORAGE_KEYS.material(id));
    });
  }

  private readIndexes(): Promise<[string[], DedupeIndex]> {
    return Promise.all([
      read<string[]>(STORAGE_KEYS.materialIndex, []),
      read<DedupeIndex>(STORAGE_KEYS.materialDedupe, {}),
    ]);
  }

  /** 写入素材并维护索引；oldId 不同于新 ID 时移除旧记录，新记录保持旧记录在列表中的位置 */
  private async write(oldId: string | null, material: NewsMaterial, dedupeKeys: string[]): Promise<void> {
    try {
      const [ids, index] = await this.readIndexes();
      const replacing = oldId !== null && oldId !== material.id;
      let nextIds = ids.filter((x) => x !== material.id);
      const pos = replacing ? nextIds.indexOf(oldId) : ids.indexOf(material.id);
      if (replacing) nextIds = nextIds.filter((x) => x !== oldId);
      if (pos >= 0) nextIds.splice(pos, 0, material.id);
      else nextIds = insertByCollectTime(nextIds, material);

      const nextIndex: DedupeIndex = replacing
        ? Object.fromEntries(Object.entries(index).map(([k, v]) => [k, v === oldId ? material.id : v]))
        : { ...index };
      for (const key of dedupeKeys) nextIndex[key] = material.id;

      await chrome.storage.local.set({
        [STORAGE_KEYS.material(material.id)]: material,
        [STORAGE_KEYS.materialIndex]: nextIds,
        [STORAGE_KEYS.materialDedupe]: nextIndex,
      });
      if (replacing) await chrome.storage.local.remove(STORAGE_KEYS.material(oldId));
    } catch (err) {
      throw new AppException('STORAGE', `保存失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  softDelete(id: string, at: string): Promise<boolean> {
    return this.serial(async () => {
      const material = await this.get(id);
      if (!material || material.deleted_at) return false;
      await chrome.storage.local.set({
        [STORAGE_KEYS.material(id)]: { ...material, deleted_at: at, updated_at: at, sync_status: 'local' },
      });
      return true;
    });
  }
}
