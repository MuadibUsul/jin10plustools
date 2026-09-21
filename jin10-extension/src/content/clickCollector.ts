import { logger } from '@/shared/logger';
import { collectFlash } from './collector';
import { isActive } from './lifecycle';
import { findFlashRoot } from './parser/jin10Parser';

/** Mac 上 Ctrl+点击等同右键，改用 ⌘ */
const IS_MAC = /Mac/i.test(navigator.platform);

export function isCollectClick(event: MouseEvent): boolean {
  return event.button === 0 && (IS_MAC ? event.metaKey : event.ctrlKey) && !event.altKey;
}

/**
 * Ctrl+左键采集快讯（Ctrl+Shift+左键 = 采集并优先 AI 分析），与右键菜单并存。
 * 只在快讯上拦截点击，其他位置保持浏览器默认行为（如 Ctrl+点击新标签页打开）。
 */
export function installClickCollector(): void {
  const busy = new WeakSet<Element>();
  document.addEventListener(
    'click',
    (event) => {
      if (!isCollectClick(event) || !(event.target instanceof Element)) return;
      // 插件更新后本脚本已失效或被新注入的脚本接手，不再拦截
      if (!isActive()) return;
      const root = findFlashRoot(event.target);
      if (!root) return;
      event.preventDefault();
      event.stopPropagation();
      if (busy.has(root)) return;
      busy.add(root);
      void collectFlash(root, event.shiftKey)
        .catch((err: unknown) => logger.warn('click collect failed', err))
        .finally(() => busy.delete(root));
    },
    { capture: true },
  );
}
