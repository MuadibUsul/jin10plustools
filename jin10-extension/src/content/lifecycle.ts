/**
 * 同一页面可能先后存在多份内容脚本（插件更新后重新注入）。
 * 新脚本启动时广播 takeover，旧脚本收到后停止处理点击与消息，保证只采集一次。
 */
const TAKEOVER_EVENT = 'plus-research:takeover';
let active = true;

export function takeOver(): void {
  document.dispatchEvent(new CustomEvent(TAKEOVER_EVENT));
  document.addEventListener(TAKEOVER_EVENT, () => {
    active = false;
  });
}

/** 本脚本仍是页面上的当前实例，且所属插件上下文有效 */
export function isActive(): boolean {
  return active && Boolean(chrome.runtime?.id);
}
