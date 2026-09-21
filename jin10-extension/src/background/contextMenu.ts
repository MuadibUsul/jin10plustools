import { JIN10_URL_PATTERN } from '@/shared/constants';
import { logger } from '@/shared/logger';
import { getSession } from '@/services/settingsStore';
import { flashBadge } from './badge';
import { sendToTabWithInject } from './injector';
import { restoreBadge } from './services';

export const MENU_IDS = {
  root: 'plus-root',
  collect: 'plus-collect',
  collectAnalyze: 'plus-collect-analyze',
  separator: 'plus-separator',
  workbench: 'plus-workbench',
} as const;

const CONTEXTS: ['page', 'selection', 'link', 'image'] = ['page', 'selection', 'link', 'image'];

/** 打开团队服务端的选稿网页；未登录时打开设置页 */
export async function openWorkbench(): Promise<void> {
  const session = await getSession();
  if (session) await chrome.tabs.create({ url: `${session.api_base_url}/` });
  else await chrome.runtime.openOptionsPage();
}

/** 菜单只在金十页面出现（documentUrlPatterns） */
export async function registerContextMenus(): Promise<void> {
  await chrome.contextMenus.removeAll();
  const base = { contexts: CONTEXTS, documentUrlPatterns: [JIN10_URL_PATTERN] };
  chrome.contextMenus.create({ ...base, id: MENU_IDS.root, title: 'PLUS Research' });
  chrome.contextMenus.create({ ...base, id: MENU_IDS.collect, parentId: MENU_IDS.root, title: '加入日报素材库' });
  chrome.contextMenus.create({
    ...base,
    id: MENU_IDS.collectAnalyze,
    parentId: MENU_IDS.root,
    title: '加入并 AI 分析',
  });
  chrome.contextMenus.create({ ...base, id: MENU_IDS.separator, parentId: MENU_IDS.root, type: 'separator' });
  chrome.contextMenus.create({ ...base, id: MENU_IDS.workbench, parentId: MENU_IDS.root, title: '打开选稿网页' });
}

export async function handleMenuClick(
  info: chrome.contextMenus.OnClickData,
  tab: chrome.tabs.Tab | undefined,
): Promise<void> {
  if (info.menuItemId === MENU_IDS.workbench) {
    await openWorkbench();
    return;
  }
  if (info.menuItemId !== MENU_IDS.collect && info.menuItemId !== MENU_IDS.collectAnalyze) return;
  if (tab?.id === undefined) return;

  // 在 PLUS 注解弹窗（iframe）内右键：顶层页面记录的目标已过期，不能采集
  if (info.frameId !== undefined && info.frameId !== 0) {
    await flashBadge('!', '#f59e0b', restoreBadge);
    logger.warn('context menu clicked inside iframe, ignored');
    return;
  }

  const res = await sendToTabWithInject(tab.id, 'COLLECT_AT_CONTEXT', {
    analyze: info.menuItemId === MENU_IDS.collectAnalyze,
  });
  if (!res.ok) {
    logger.warn('collect failed', res.error);
    // 页面未注入脚本时无法显示页面提示，用图标角标提醒
    if (res.error.code === 'NO_RECEIVER') await flashBadge('!', '#ef4444', restoreBadge);
  }
}
