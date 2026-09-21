import { beijingDayKey } from '@/shared/time';
import type { NewsMaterial } from '@/types/material';

const BRAND = '#1fa2e8';

/** 工具栏图标角标显示今日采集数（北京时间） */
export async function refreshBadge(materials: NewsMaterial[]): Promise<void> {
  const today = beijingDayKey();
  const count = materials.filter((m) => beijingDayKey(new Date(m.collect_time)) === today).length;
  await chrome.action.setBadgeBackgroundColor({ color: BRAND });
  await chrome.action.setBadgeText({ text: count > 0 ? String(Math.min(count, 999)) : '' });
}

/** 短暂显示提示角标，随后恢复 */
export async function flashBadge(text: string, color: string, restore?: () => Promise<void>): Promise<void> {
  await chrome.action.setBadgeBackgroundColor({ color });
  await chrome.action.setBadgeText({ text });
  setTimeout(() => {
    void (restore ? restore() : chrome.action.setBadgeText({ text: '' }));
  }, 3000);
}
