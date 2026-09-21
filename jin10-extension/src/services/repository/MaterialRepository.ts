import type { NewsMaterial } from '@/types/material';

/**
 * 素材存储接口。
 * 当前实现为 chrome.storage.local（LocalMaterialRepository）；
 * P4 接入服务端后，远程实现遵循同一接口，业务层无需改动。
 */
export interface MaterialRepository {
  /** 未删除的素材，按采集时间倒序 */
  list(): Promise<NewsMaterial[]>;
  get(id: string): Promise<NewsMaterial | null>;
  /** 按去重键查找（包含已软删除的素材） */
  findByDedupeKeys(keys: string[]): Promise<NewsMaterial | null>;
  /** 新增或覆盖，并登记去重键 */
  save(material: NewsMaterial, dedupeKeys: string[]): Promise<void>;
  /** 软删除（保留墓碑以便同步到服务端） */
  softDelete(id: string, at: string): Promise<boolean>;
  /** 全部记录（含已删除的墓碑），供同步使用 */
  listAll(): Promise<NewsMaterial[]>;
  /** 用服务端版本替换本地记录；ID 可能变化（同事已收藏过同一条时，采用团队既有 ID） */
  replace(oldId: string, material: NewsMaterial, dedupeKeys: string[]): Promise<void>;
  /** 彻底移除本地记录 */
  remove(id: string): Promise<void>;
}
