import { createMessageListener } from '@/shared/messaging';
import { logger } from '@/shared/logger';
import { nowIso } from '@/shared/time';
import { collectAtContext } from './collector';
import { installContextTracker } from './contextTracker';
import { scanFlashItems } from './parser/jin10Parser';

installContextTracker();

chrome.runtime.onMessage.addListener(
  createMessageListener({
    PING: () => ({
      context: 'content',
      version: chrome.runtime.getManifest().version,
      at: nowIso(),
    }),
    SCAN_PAGE: () => scanFlashItems(document, { pageUrl: location.href, now: new Date() }),
    COLLECT_AT_CONTEXT: ({ analyze }) => collectAtContext(analyze),
  }),
);

logger.info('content script ready', location.href);
