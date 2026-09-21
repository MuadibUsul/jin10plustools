import { JIN10_URL_PATTERN } from '@/shared/constants';
import { logger } from '@/shared/logger';
import { sendToTab } from '@/shared/messaging';
import type { MessageRequest, MessageResponse, MessageType, Result } from '@/types/messages';

/** 构建后的内容脚本路径（CRXJS 生成的 loader），从运行时 manifest 读取 */
function contentScriptFiles(): string[] {
  return chrome.runtime.getManifest().content_scripts?.flatMap((cs) => cs.js ?? []) ?? [];
}

/**
 * 插件安装 / 更新 / 重新加载后，已打开页面里的旧内容脚本会失效（右键、Ctrl+点击都没反应），
 * 这里重新注入，免去手动刷新页面。
 */
export async function injectContentScript(tabId: number): Promise<boolean> {
  const files = contentScriptFiles();
  if (!files.length) return false;
  try {
    await chrome.scripting.executeScript({ target: { tabId, frameIds: [0] }, files });
    return true;
  } catch (err) {
    logger.warn('inject content script failed', tabId, err);
    return false;
  }
}

export async function injectIntoOpenJin10Tabs(): Promise<void> {
  const tabs = await chrome.tabs.query({ url: JIN10_URL_PATTERN });
  await Promise.all(tabs.map((t) => (t.id === undefined ? null : injectContentScript(t.id))));
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 发给页面；页面脚本失效时重新注入后重试（loader 异步加载，稍等片刻） */
export async function sendToTabWithInject<K extends MessageType>(
  tabId: number,
  type: K,
  payload: MessageRequest<K>,
): Promise<Result<MessageResponse<K>>> {
  const first = await sendToTab(tabId, type, payload);
  if (first.ok || first.error.code !== 'NO_RECEIVER') return first;
  if (!(await injectContentScript(tabId))) return first;
  for (let i = 0; i < 10; i++) {
    await sleep(200);
    const res = await sendToTab(tabId, type, payload);
    if (res.ok || res.error.code !== 'NO_RECEIVER') return res;
  }
  return first;
}
