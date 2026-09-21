/**
 * chrome.contextMenus.onClicked 拿不到被右键的 DOM，
 * 所以在捕获阶段记录最近一次右键的元素，菜单点击时再取用。
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
