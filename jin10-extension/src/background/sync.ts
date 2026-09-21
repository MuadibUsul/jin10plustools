import { logger } from '@/shared/logger';
import { createSyncService } from '@/services/syncService';
import { materialRepository, restoreBadge } from './services';

export const SYNC_ALARM = 'plus-sync-periodic';
const SYNC_PERIOD_MINUTES = 5;
/** 采集后稍等片刻再同步，连续采集时合并为一次 */
const SOON_DELAY_MS = 1500;

export const syncService = createSyncService(materialRepository);

let soonTimer: ReturnType<typeof setTimeout> | undefined;

export async function syncNow() {
  const report = await syncService.sync();
  await restoreBadge();
  return report;
}

export function syncSoon(): void {
  clearTimeout(soonTimer);
  soonTimer = setTimeout(() => {
    void syncNow().catch((err: unknown) => logger.warn('background sync', err));
  }, SOON_DELAY_MS);
}

export function ensureSyncAlarm(): Promise<void> {
  return chrome.alarms.create(SYNC_ALARM, { periodInMinutes: SYNC_PERIOD_MINUTES });
}
