import { createMessageListener } from '@/shared/messaging';
import { logger } from '@/shared/logger';
import { handleMenuClick, registerContextMenus } from './contextMenu';
import { injectIntoOpenJin10Tabs } from './injector';
import { backgroundHandlers } from './messageRouter';
import { restoreBadge } from './services';
import { ensureSyncAlarm, SYNC_ALARM, syncSoon } from './sync';

// MV3 service worker 会被随时回收：所有监听器必须在顶层同步注册，且不依赖内存状态。
chrome.runtime.onMessage.addListener(createMessageListener(backgroundHandlers));

chrome.contextMenus.onClicked.addListener((info, tab) => {
  void handleMenuClick(info, tab).catch((err: unknown) => logger.error('menu click', err));
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === SYNC_ALARM) syncSoon();
});

chrome.runtime.onInstalled.addListener(({ reason }) => {
  logger.info('installed', reason);
  void registerContextMenus();
  void restoreBadge();
  void ensureSyncAlarm();
  // 安装 / 更新 / 重新加载后，已打开的金十页面无需刷新即可采集
  void injectIntoOpenJin10Tabs().catch((err: unknown) => logger.warn('inject on install', err));
});

chrome.runtime.onStartup.addListener(() => {
  void restoreBadge();
  void ensureSyncAlarm();
  syncSoon();
});
