import type { HandlerMap } from '@/shared/messaging';
import { nowIso } from '@/shared/time';
import { login } from '@/services/api/serverApi';
import { clearSession, setLastApiBaseUrl, setSession } from '@/services/settingsStore';
import { materialService, restoreBadge } from './services';
import { syncNow, syncSoon } from './sync';

export const backgroundHandlers: HandlerMap = {
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
