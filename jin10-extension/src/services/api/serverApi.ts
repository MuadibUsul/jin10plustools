/** 团队服务端接口（与 jin10-server/app/api 对应） */
import type { ImageAsset, NewsMaterial } from '@/types/material';
import type { AuthSession, SessionTeam, SessionUser } from '@/types/settings';
import { apiRequest } from './apiClient';

/** 服务端素材 = 本地素材字段 + 服务端维护字段 */
export type ServerMaterial = Omit<NewsMaterial, 'fingerprint' | 'sync_status' | 'collector_id'> & {
  fingerprint: string;
  created_by: string | null;
};

export interface SyncResult {
  client_id: string;
  outcome: 'created' | 'merged' | 'unchanged' | 'deleted';
  material: ServerMaterial | null;
}

interface LoginResponse {
  access_token: string;
  expires_at: string;
  user: SessionUser;
  team: SessionTeam;
}

export async function login(apiBaseUrl: string, email: string, password: string): Promise<AuthSession> {
  const res = await apiRequest<LoginResponse>('/api/auth/login', {
    method: 'POST',
    baseUrl: apiBaseUrl,
    json: { email, password },
  });
  return { api_base_url: apiBaseUrl, ...res };
}

/** 本地素材 → 推送载荷（只含采集端负责的字段；AI / 编辑字段由服务端维护） */
export function toMaterialIn(m: NewsMaterial) {
  return {
    id: m.id,
    source: m.source,
    source_item_id: m.source_item_id,
    fingerprint: m.fingerprint,
    item_kind: m.item_kind,
    locked: m.locked,
    tags: m.tags,
    detail_url: m.detail_url,
    title: m.title,
    flash_title: m.flash_title,
    content: m.content,
    content_raw: m.content_raw,
    plus_title: m.plus_title,
    has_plus_link: m.has_plus_link,
    images: m.images,
    publish_time: m.publish_time,
    url: m.url,
    raw_html: m.raw_html,
    collect_time: m.collect_time,
    collect_method: m.collect_method,
    interpretation_status: m.interpretation_status,
    interpretation: m.interpretation,
    analyze_requested: m.analyze_requested,
    deleted_at: m.deleted_at,
  };
}

/** 服务端素材 → 本地记录（标记为已同步） */
export function fromServer(s: ServerMaterial): NewsMaterial {
  const { created_by, ...rest } = s;
  return { ...rest, collector_id: created_by, sync_status: 'synced', schema_version: 1 };
}

export async function pushMaterials(materials: NewsMaterial[]): Promise<SyncResult[]> {
  const res = await apiRequest<{ results: SyncResult[] }>('/api/materials/sync', {
    method: 'POST',
    json: { materials: materials.map(toMaterialIn) },
  });
  return res.results;
}

export function fetchChanges(cursor: string | null, limit = 200) {
  const params = new URLSearchParams({ limit: String(limit) });
  if (cursor) params.set('cursor', cursor);
  return apiRequest<{ items: ServerMaterial[]; next_cursor: string | null; has_more: boolean }>(
    `/api/materials/changes?${params}`,
  );
}

export async function missingAssets(ids: string[]): Promise<string[]> {
  if (ids.length === 0) return [];
  const res = await apiRequest<{ missing: string[] }>('/api/assets/exists', { method: 'POST', json: { ids } });
  return res.missing;
}

export async function uploadAsset(asset: ImageAsset): Promise<void> {
  const blob = await (await fetch(asset.data_url)).blob();
  await apiRequest<void>(`/api/assets/${asset.id}`, {
    method: 'PUT',
    body: blob,
    headers: { 'Content-Type': asset.mime, 'X-Source-Url': encodeURIComponent(asset.source_url) },
  });
}
