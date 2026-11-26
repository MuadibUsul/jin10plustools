/**
 * 同一页面可能先后存在多份内容脚本（插件更新后重新注入）。
 * 新脚本启动时广播 takeover，旧脚本收到后停止处理点击与消息并卸载监听，保证只采集一次。
 */
const TAKEOVER_EVENT = 'plus-research:takeover';
let active = true;
let controller = new AbortController();

export function takeOver(): void {
  document.dispatchEvent(new CustomEvent(TAKEOVER_EVENT));
  active = true;
  controller = new AbortController();
  const currentController = controller;
  document.addEventListener(
    TAKEOVER_EVENT,
    () => {
      if (controller === currentController) active = false;
      currentController.abort();
    },
    { once: true },
  );
}

export function lifecycleSignal(): AbortSignal {
  return controller.signal;
}

export function onLifecycleAbort(callback: () => void): void {
  if (controller.signal.aborted) {
    callback();
    return;
  }
  controller.signal.addEventListener('abort', callback, { once: true });
}

/** 本脚本仍是页面上的当前实例，且所属插件上下文有效 */
export function isActive(): boolean {
  return active && !controller.signal.aborted && Boolean(chrome.runtime?.id);
}
