import { beforeEach, describe, expect, it } from 'vitest';
import { findFlashRoot, parseFlashItem, scanFlashItems } from '@/content/parser/jin10Parser';
import plusItem from './fixtures/jin10-flash-plus.html?raw';
import liveList from './fixtures/jin10-list-live.html?raw';

/** 无标题普通快讯（线上约 1/3 快讯没有 .right-common-title） */
const noTitleItem = `
<div class="jin-flash-item-container is-normal" id="flash20260921150102800100">
  <div class="jin-flash-item flash">
    <div class="flash-item-header"><div class="item-time"><span>15:01:02</span></div></div>
    <div class="item-right is-common">
      <div class="right-top"><div class="right-content"><div class="flash-text">金十数据9月21日讯，美元指数短线走高，日内涨0.2%。</div></div></div>
    </div>
  </div>
</div>`;

const ctx = { pageUrl: 'https://www.jin10.com/', now: new Date('2026-09-21T10:00:00Z') };

beforeEach(() => {
  document.body.innerHTML = `<div id="list">${plusItem}${liveList}${noTitleItem}</div>`;
});

function el(selector: string): Element {
  const found = document.querySelector(selector);
  if (!found) throw new Error(`missing ${selector}`);
  return found;
}

function parseAt(selector: string) {
  const res = parseFlashItem(findFlashRoot(el(selector))!, ctx);
  if (!res.ok) throw new Error(res.reason);
  return res.draft;
}

describe('findFlashRoot', () => {
  it('从正文、标题、PLUS 按钮、时间任意位置都能定位到整条快讯', () => {
    for (const sel of ['.flash-text', '.right-common-title', '.plus-link-title', '.item-time span']) {
      expect(findFlashRoot(el(sel))?.classList.contains('jin-flash-item')).toBe(true);
    }
  });

  it('快讯之外的元素返回 null', () => {
    expect(findFlashRoot(el('#list'))).toBeNull();
    expect(findFlashRoot(null)).toBeNull();
  });
});

describe('普通快讯 + PLUS 注解（用户提供的真实 HTML）', () => {
  const parse = () => parseAt('.plus-link-title');

  it('提取标题、PLUS 标题，展示标题优先使用 PLUS 标题', () => {
    const d = parse();
    expect(d.item_kind).toBe('flash');
    expect(d.flash_title).toBe('研究：采用AI的公司招聘数量更多，但增量显著偏向资深员工');
    expect(d.plus_title).toBe('AI重塑劳动力市场的结构');
    expect(d.title).toBe('AI重塑劳动力市场的结构');
    expect(d.has_plus_link).toBe(true);
    expect(d.locked).toBe(false);
  });

  it('正文去掉「金十数据X月X日讯」前缀，原文保留', () => {
    const d = parse();
    expect(d.content.startsWith('一项覆盖41个国家的新研究显示')).toBe(true);
    expect(d.content.endsWith('初级员工就业流失更为严重。')).toBe(true);
    expect(d.content_raw.startsWith('金十数据9月21日讯，')).toBe(true);
  });

  it('无条目 ID 时，发布时间 = 正文日期 + 条目时间', () => {
    const d = parse();
    expect(d.source_item_id).toBeNull();
    expect(d.publish_time).toBe('2026-09-21T16:23:37+08:00');
  });

  it('会员图标不算配图；raw_html 去掉 svg / 注释 / data-v / style', () => {
    const d = parse();
    expect(d.images).toEqual([]);
    expect(d.raw_html).not.toMatch(/<svg|<!--|data-v-|style=/);
    expect(d.raw_html).toContain('flash-text');
  });
});

describe('文章卡片（线上实测结构）', () => {
  const parse = () => parseAt('#flash20260921170417801800 .right-content_intro');

  it('提取标题（去掉标签）、摘要（去掉「点击查看」）、详情链接与标签', () => {
    const d = parse();
    expect(d.item_kind).toBe('article');
    expect(d.flash_title).toBe('无惧美联储重压！铜价逆势狂飙背后，有人疯狂“抢货”');
    expect(d.title).toBe(d.flash_title);
    expect(d.content.endsWith('正以前所未有的速度驶向美国')).toBe(true);
    expect(d.detail_url).toBe('https://xnews.jin10.com/details/230625');
    expect(d.tags).toEqual(['市场要闻']);
  });

  it('条目 ID 解析出完整发布时间；懒加载配图去掉 /ncover 取原图', () => {
    const d = parse();
    expect(d.source_item_id).toBe('flash20260921170417801800');
    expect(d.publish_time).toBe('2026-09-21T17:04:17+08:00');
    expect(d.images).toEqual([
      { url: 'https://gimg.jin10.com/news/26/01/1TT7c33yGJtKStsbQmCml.png', source: 'flash', asset_id: null },
    ]);
  });
});

describe('VIP 未解锁快讯（线上实测结构）', () => {
  it('标记为锁定，正文不含 VIP 标签与解锁按钮文字', () => {
    const d = parseAt('#flash20260921165503668800 .flash-text');
    expect(d.item_kind).toBe('vip');
    expect(d.locked).toBe(true);
    expect(d.content).toBe('为何高盛认为日元没有太多走强的空间？');
    expect(d.title).toBe('为何高盛认为日元没有太多走强的空间');
    expect(d.tags).toEqual(['VIP']);
  });
});

describe('无标题快讯', () => {
  it('用正文首句作为标题', () => {
    const d = parseAt('#flash20260921150102800100 .flash-text');
    expect(d.flash_title).toBeNull();
    expect(d.title).toBe('美元指数短线走高，日内涨0.2%');
    expect(d.publish_time).toBe('2026-09-21T15:01:02+08:00');
  });

  it('正文为空时返回失败原因', () => {
    el('#flash20260921150102800100 .flash-text').textContent = '  ';
    const res = parseFlashItem(el('#flash20260921150102800100 .jin-flash-item'), ctx);
    expect(res.ok).toBe(false);
  });
});

describe('scanFlashItems', () => {
  it('批量识别页面上的全部快讯', () => {
    const res = scanFlashItems(document, ctx);
    expect(res.total).toBe(4);
    expect(res.drafts.map((d) => d.item_kind)).toEqual(['flash', 'article', 'vip', 'flash']);
    expect(res.failures).toEqual([]);
  });
});
