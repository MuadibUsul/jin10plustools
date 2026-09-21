import { useEffect, useState } from 'react';
import type { AuthSession, SyncState } from '@/types/settings';
import { STORAGE_KEYS } from '@/shared/constants';
import { getSession, getSyncState } from '@/services/settingsStore';

/** 只读订阅登录会话与同步状态 */
export function useTeamSession(): { session: AuthSession | null; sync: SyncState | null } {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [sync, setSync] = useState<SyncState | null>(null);

  useEffect(() => {
    const load = () => {
      void getSession().then(setSession);
      void getSyncState().then(setSync);
    };
    const onChanged = (changes: Record<string, chrome.storage.StorageChange>, area: string) => {
      if (area === 'local' && (STORAGE_KEYS.authSession in changes || STORAGE_KEYS.syncState in changes)) load();
    };
    load();
    chrome.storage.onChanged.addListener(onChanged);
    return () => chrome.storage.onChanged.removeListener(onChanged);
  }, []);

  return { session, sync };
}
