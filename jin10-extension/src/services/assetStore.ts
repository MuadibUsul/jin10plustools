import type { ImageAsset } from '@/types/material';
import type { ImageUpload } from '@/types/messages';
import { STORAGE_KEYS } from '@/shared/constants';
import { sha256Hex } from '@/shared/hash';
import { nowIso } from '@/shared/time';

/**
 * 图片文件存储（chrome.storage.local，已申请 unlimitedStorage）。
 * ID 由原图地址哈希得到，同一张图只存一份；P4 接入后上传到服务端文件存储。
 */
export async function assetIdForUrl(url: string): Promise<string> {
  return (await sha256Hex(url)).slice(0, 32);
}

export async function putImage(upload: ImageUpload): Promise<string> {
  const id = await assetIdForUrl(upload.url);
  const key = STORAGE_KEYS.asset(id);
  const existing = await chrome.storage.local.get(key);
  if (!existing[key]) {
    const asset: ImageAsset = {
      id,
      source_url: upload.url,
      mime: upload.mime,
      size: upload.size,
      data_url: upload.data_url,
      created_at: nowIso(),
    };
    await chrome.storage.local.set({ [key]: asset });
  }
  return id;
}

export async function getImage(id: string): Promise<ImageAsset | null> {
  const key = STORAGE_KEYS.asset(id);
  const result = await chrome.storage.local.get(key);
  return (result[key] as ImageAsset | undefined) ?? null;
}
