import type { HandlerMap } from '@/shared/messaging';
import { nowIso } from '@/shared/time';
import { latestExtensionVersion, login, reportParse } from '@/services/api/serverApi';
import { getSession } from '@/services/settingsStore';
import type { UpdateInfo } from '@/types/messages';
import { clearSession, setLastApiBaseUrl, setSession } from '@/services/settingsStore';
import { materialService, restoreBadge } from './services';
import { syncNow, syncSoon } from './sync';

const UPDATE_CHECK_MS = 30 * 60_000;
let updateCache: { at: number; info: UpdateInfo } | null = null;

/** 版本号逐段按数字比较：a < b 返回 true */
function older(a: string, b: string): boolean {
  const pa = a.split('.').map(Number);
  const pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return d < 0;
  }
  return false;
}

async function checkUpdate(): Promise<UpdateInfo> {
  if (updateCache && Date.now() - updateCache.at < UPDATE_CHECK_MS) return updateCache.info;
  const current = chrome.runtime.getManifest().version;
  const session = await getSession();
  let latest: string | null = null;
  if (session) latest = await latestExtensionVersion().catch(() => null);
  const info: UpdateInfo = {
    current,
    latest,
    outdated: latest !== null && older(current, latest),
    download_url: session ? `${session.api_base_url}/plus-research-extension.zip` : null,
  };
  updateCache = { at: Date.now(), info };
  return info;
}

export const backgroundHandlers: HandlerMap = {
  CHECK_UPDATE: () => checkUpdate(),

  REPORT_PARSE: async (report) => {
    if (await getSession()) await reportParse(report, chrome.runtime.getManifest().version).catch(() => undefined);
  },

  PING: () => ({
    context: 'background',
    version: chrome.runtime.getManifest().version,
    at: nowIso(),
  }),

  SAVE_MATERIAL: async (payload) => {
    const result = await materialService.save(payload);
    await restoreBadge();
    if (result.outcome !== 'duplicate') syncSoon();
    return result;
  },

  DELETE_MATERIAL: async ({ id }) => {
    const deleted = await materialService.delete(id);
    await restoreBadge();
    if (deleted) syncSoon();
    return { deleted };
  },

  LOGIN: async ({ api_base_url, email, password }) => {
    const session = await login(api_base_url, email.trim(), password);
    await setSession(session);
    await setLastApiBaseUrl(api_base_url);
    syncSoon();
    return session;
  },

  LOGOUT: async () => {
    await clearSession();
  },

  SYNC_NOW: () => syncNow(),
};
