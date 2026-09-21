import type { ImageSource } from '@/types/material';
import { logger } from '@/shared/logger';

/** 单张图片上限，超过则只保留链接 */
const MAX_IMAGE_BYTES = 15 * 1024 * 1024;
const TIMEOUT_MS = 15000;

export interface DownloadedImage {
  url: string;
  source: ImageSource;
  mime: string;
  size: number;
  data_url: string;
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error ?? new Error('读取图片失败'));
    reader.readAsDataURL(blob);
  });
}

/**
 * 在金十页面上下文中下载图片（金十图床对 www.jin10.com 开放 CORS，2026-09-21 实测）。
 * 失败返回 null，素材仍保留图片链接。
 */
export async function downloadImage(url: string, source: ImageSource): Promise<DownloadedImage | null> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, { signal: controller.signal, credentials: 'omit' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = await res.blob();
    if (!blob.type.startsWith('image/')) throw new Error(`非图片类型 ${blob.type}`);
    if (blob.size > MAX_IMAGE_BYTES) throw new Error(`图片过大 ${blob.size}`);
    return { url, source, mime: blob.type, size: blob.size, data_url: await blobToDataUrl(blob) };
  } catch (err) {
    logger.warn('image download failed', url, err);
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export async function downloadImages(
  images: { url: string; source: ImageSource }[],
): Promise<DownloadedImage[]> {
  const results = await Promise.all(images.map((img) => downloadImage(img.url, img.source)));
  return results.filter((r): r is DownloadedImage => r !== null);
}
