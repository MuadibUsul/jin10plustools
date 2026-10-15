import type { AuthSession, SyncState } from '@/types/settings';
import { STORAGE_KEYS } from '@/shared/constants';
import { DEFAULT_CAPTURE_SHORTCUT, type CaptureShortcut } from '@/shared/shortcut';

const DEFAULT_SYNC: SyncState = {
  cursor: null,
  team_id: null,
  last_sync_at: null,
  last_error: null,
  syncing: false,
};

async function read<T>(key: string): Promise<T | undefined> {
  const result = await chrome.storage.local.get(key);
  return result[key] as T | undefined;
}

export async function getSession(): Promise<AuthSession | null> {
  const session = await read<AuthSession>(STORAGE_KEYS.authSession);
  if (!session) return null;
  if (Date.parse(session.expires_at) <= Date.now()) return null;
  return session;
}

export function setSession(session: AuthSession): Promise<void> {
  return chrome.storage.local.set({ [STORAGE_KEYS.authSession]: session });
}

export function clearSession(): Promise<void> {
  return chrome.storage.local.remove(STORAGE_KEYS.authSession);
}

/** 上次成功登录的服务端地址，登录表单默认填入 */
export async function getLastApiBaseUrl(): Promise<string> {
  return (await read<string>(STORAGE_KEYS.lastApiBaseUrl)) ?? '';
}

export function setLastApiBaseUrl(url: string): Promise<void> {
  return chrome.storage.local.set({ [STORAGE_KEYS.lastApiBaseUrl]: url });
}

export async function getCaptureShortcut(): Promise<CaptureShortcut> {
  const saved = await read<Partial<CaptureShortcut>>(STORAGE_KEYS.captureShortcut);
  return {
    primary: saved?.primary ?? DEFAULT_CAPTURE_SHORTCUT.primary,
    alt: saved?.alt ?? DEFAULT_CAPTURE_SHORTCUT.alt,
    shift: saved?.shift ?? DEFAULT_CAPTURE_SHORTCUT.shift,
  };
}

export function setCaptureShortcut(shortcut: CaptureShortcut): Promise<void> {
  return chrome.storage.local.set({ [STORAGE_KEYS.captureShortcut]: shortcut });
}

export async function getSyncState(): Promise<SyncState> {
  return { ...DEFAULT_SYNC, ...(await read<Partial<SyncState>>(STORAGE_KEYS.syncState)) };
}

export async function updateSyncState(patch: Partial<SyncState>): Promise<SyncState> {
  const next = { ...(await getSyncState()), ...patch };
  await chrome.storage.local.set({ [STORAGE_KEYS.syncState]: next });
  return next;
}

/** 规范化服务端地址：去掉末尾斜杠与路径，只保留 origin */
export function normalizeApiBaseUrl(input: string): string {
  const url = new URL(input.trim());
  if (url.protocol !== 'https:' && url.protocol !== 'http:') throw new Error('服务端地址需以 http:// 或 https:// 开头');
  return url.origin;
}
