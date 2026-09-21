/**
 * chrome.contextMenus.onClicked 拿不到被点击的 DOM，
 * 因此在捕获阶段记录最近一次右键的目标元素，供采集时使用。
 */
let lastTarget: Element | null = null;

export function installContextTracker(): void {
  document.addEventListener(
    'contextmenu',
    (event) => {
      lastTarget = event.target instanceof Element ? event.target : null;
    },
    { capture: true, passive: true },
  );
}

export function getLastContextTarget(): Element | null {
  return lastTarget?.isConnected ? lastTarget : null;
}
