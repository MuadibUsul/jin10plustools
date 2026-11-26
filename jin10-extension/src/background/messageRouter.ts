import type { HandlerMap } from '@/shared/messaging';
import { STORAGE_KEYS } from '@/shared/constants';
import { nowIso } from '@/shared/time';
import { latestExtensionVersion, login, reportParse } from '@/services/api/serverApi';
import { getLastApiBaseUrl, getSession, getSyncState, setLastApiBaseUrl } from '@/services/settingsStore';
import type { UpdateInfo } from '@/types/messages';
import { materialService, materialRepository, restoreBadge } from './services';
import { syncNow, syncSoon } from './sync';
import { withAuthLock } from '@/services/sessionScope';
import { activateScope, deactivateScope } from '@/services/syncService';

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

  SAVE_MATERIAL: (payload) => withAuthLock(async () => {
    const session = await getSession();
    if (session) await activateScope(materialRepository, session);
    else if ((await getSyncState()).team_id) await deactivateScope(materialRepository, await getLastApiBaseUrl());
    const result = await materialService.save(payload);
    await restoreBadge();
    if (session && result.outcome !== 'duplicate') syncSoon();
    return result;
  }),

  DELETE_MATERIAL: ({ id }) => withAuthLock(async () => {
    const session = await getSession();
    if (session) await activateScope(materialRepository, session);
    else if ((await getSyncState()).team_id) await deactivateScope(materialRepository, await getLastApiBaseUrl());
    const deleted = await materialService.delete(id);
    await restoreBadge();
    if (session && deleted) syncSoon();
    return { deleted };
  }),

  LOGIN: async ({ api_base_url, email, password }) => {
    const session = await login(api_base_url, email.trim(), password);
    await setLastApiBaseUrl(api_base_url);
    await withAuthLock(async () => {
      await chrome.storage.local.set({
        [STORAGE_KEYS.authSession]: session,
        [STORAGE_KEYS.syncState]: { ...(await getSyncState()), syncing: false, sync_started_at: null, last_error: null },
      });
      await activateScope(materialRepository, session);
      await restoreBadge();
    });
    syncSoon();
    return session;
  },

  LOGOUT: async () => {
    await withAuthLock(async () => {
      const prev = await getSession();
      await chrome.storage.local.remove(STORAGE_KEYS.authSession);
      await deactivateScope(materialRepository, prev?.api_base_url || (await getLastApiBaseUrl()));
      await restoreBadge();
    });
  },

  SYNC_NOW: () => syncNow(),
};
