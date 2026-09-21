import { createMessageListener } from '@/shared/messaging';
import { logger } from '@/shared/logger';
import { nowIso } from '@/shared/time';
import { collectAtContext } from './collector';
import { installClickCollector } from './clickCollector';
import { installContextTracker } from './contextTracker';
import { isActive, takeOver } from './lifecycle';
import { scanFlashItems } from './parser/jin10Parser';

takeOver();
installContextTracker();
installClickCollector();

const handle = createMessageListener({
  PING: () => ({
    context: 'content',
    version: chrome.runtime.getManifest().version,
    at: nowIso(),
  }),
  SCAN_PAGE: () => scanFlashItems(document, { pageUrl: location.href, now: new Date() }),
  COLLECT_AT_CONTEXT: ({ analyze }) => collectAtContext(analyze),
});

// 被新注入的脚本接手后不再响应，避免重复采集
chrome.runtime.onMessage.addListener((message, sender, sendResponse) =>
  isActive() ? handle(message, sender, sendResponse) : false,
);

logger.info('content script ready', location.href);
