import type { NewsMaterial } from '@/types/material';
import { createSerialQueue } from '@/shared/async';
import { STORAGE_KEYS } from '@/shared/constants';
import { AppException } from '@/shared/errors';
import type { MaterialRepository } from './MaterialRepository';

type DedupeIndex = Record<string, string>;

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
    return (await this.listAll()).filter((m) => m.deleted_at === null);
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
