import { logger } from '@/shared/logger';
import { collectFlash } from './collector';
import { isActive } from './lifecycle';
import { findFlashRoot } from './parser/jin10Parser';
import { getCaptureShortcut } from '@/services/settingsStore';
import { DEFAULT_CAPTURE_SHORTCUT, matchesCaptureClick, type CaptureShortcut } from '@/shared/shortcut';

let shortcut: CaptureShortcut = DEFAULT_CAPTURE_SHORTCUT;

export function isCollectClick(event: MouseEvent, configured = DEFAULT_CAPTURE_SHORTCUT): boolean {
  const primary = /Mac/i.test(navigator.platform) ? event.metaKey : event.ctrlKey;
  const legacyAnalyze = event.button === 0 && primary && event.shiftKey && !event.altKey;
  return matchesCaptureClick(event, configured) || legacyAnalyze;
}

/**
 * 配置的修饰键+左键采集快讯（Ctrl+Shift+左键仍兼容为采集并优先 AI 分析），与右键菜单并存。
 * 只在快讯上拦截点击，其他位置保持浏览器默认行为（如 Ctrl+点击新标签页打开）。
 */
export function installClickCollector(): void {
  void getCaptureShortcut().then((value) => { shortcut = value; });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !changes['settings:captureShortcut']?.newValue) return;
    shortcut = changes['settings:captureShortcut'].newValue as CaptureShortcut;
  });

  const busy = new WeakSet<Element>();
  document.addEventListener(
    'click',
    (event) => {
      if (!isCollectClick(event, shortcut) || !(event.target instanceof Element)) return;
      // 插件更新后本脚本已失效或被新注入的脚本接手，不再拦截
      if (!isActive()) return;
      const root = findFlashRoot(event.target);
      if (!root) return;
      event.preventDefault();
      event.stopPropagation();
      if (busy.has(root)) return;
      busy.add(root);
      const primary = /Mac/i.test(navigator.platform) ? event.metaKey : event.ctrlKey;
      const analyze = event.button === 0 && primary && event.shiftKey && !event.altKey && !matchesCaptureClick(event, shortcut);
      void collectFlash(root, analyze)
        .catch((err: unknown) => logger.warn('click collect failed', err))
        .finally(() => busy.delete(root));
    },
    { capture: true },
  );
}
