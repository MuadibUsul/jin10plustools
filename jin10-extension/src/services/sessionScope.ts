import type { AuthSession } from '@/types/settings';
import { createSerialQueue } from '@/shared/async';
import { getSession } from './settingsStore';

// Login changes and local sync commits share one boundary in the background worker.
export const withAuthLock = createSerialQueue();
export class SessionChanged extends Error {}

export function sameSession(a: AuthSession | null, b: AuthSession): boolean {
  return !!a && a.team.id === b.team.id && a.user.id === b.user.id &&
    a.api_base_url === b.api_base_url && a.access_token === b.access_token;
}

export function inSession<T>(session: AuthSession, action: () => Promise<T>): Promise<T> {
  return withAuthLock(async () => {
    if (!sameSession(await getSession(), session)) throw new SessionChanged('登录已切换');
    return action();
  });
}
