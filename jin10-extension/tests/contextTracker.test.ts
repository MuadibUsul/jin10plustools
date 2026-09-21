import { describe, expect, it } from 'vitest';
import { isCollectClick } from '@/content/clickCollector';
import { getLastContextTarget, installContextTracker } from '@/content/contextTracker';

describe('采集触发', () => {
  it('记录最近一次右键的元素，元素移除后失效', () => {
    installContextTracker();
    document.body.innerHTML = '<div id="a"></div>';
    const a = document.getElementById('a')!;
    a.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true }));
    expect(getLastContextTarget()).toBe(a);
    a.remove();
    expect(getLastContextTarget()).toBeNull();
  });

  it('只有 Ctrl+左键（可加 Shift）算采集点击', () => {
    expect(isCollectClick(new MouseEvent('click', { button: 0, ctrlKey: true }))).toBe(true);
    expect(isCollectClick(new MouseEvent('click', { button: 0, ctrlKey: true, shiftKey: true }))).toBe(true);
    expect(isCollectClick(new MouseEvent('click', { button: 0 }))).toBe(false);
    expect(isCollectClick(new MouseEvent('click', { button: 0, ctrlKey: true, altKey: true }))).toBe(false);
    expect(isCollectClick(new MouseEvent('click', { button: 1, ctrlKey: true }))).toBe(false);
  });
});
