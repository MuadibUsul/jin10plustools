import { describe, expect, it } from 'vitest';
import { parseAnnotation, parsePlusId } from '@/content/plus/annotationParser';
import observationHtml from './fixtures/plus-annotation-observation.html?raw';
import viewHtml from './fixtures/plus-annotation-view.html?raw';

const BASE = 'https://www.jin10.com/feature/2026/06/08/plus/index.html#/?id=6620';

function parse(html: string) {
  document.body.innerHTML = html;
  const result = parseAnnotation(document, BASE);
  if (!result) throw new Error('parse failed');
  return result;
}

describe('parseAnnotation', () => {
  it('「PLUS观点」模块：按换行拆段，排除装饰插画与免责声明', () => {
    const r = parse(viewHtml);
    expect(r.title).toBe('企业AI补齐交付链条');
    expect(r.modules).toHaveLength(1);
    const [m] = r.modules;
    expect(m?.kind).toBe('view');
    expect(m?.heading).toBe('PLUS观点');
    expect(m?.paragraphs).toHaveLength(2);
    expect(m?.paragraphs[1]).toBe('双方合作的看点是把前端智能体与底层交付、安全和行业知识结合。');
    expect(m?.images).toEqual([]);
    expect(JSON.stringify(r)).not.toContain('仅供参考');
  });

  it('「重点观察」模块：逐条要点（不含编号），配图去掉 /lite 取原图', () => {
    const r = parse(observationHtml);
    const [m] = r.modules;
    expect(m?.kind).toBe('observation');
    expect(m?.heading).toBe('重点观察');
    expect(m?.paragraphs).toHaveLength(3);
    expect(m?.paragraphs[0]?.startsWith('过去英国固定收益养老金')).toBe(true);
    expect(m?.paragraphs.some((p) => /^0\d/.test(p))).toBe(false);
    expect(m?.images).toEqual([
      { url: 'https://img.jin10.com/misc/26/09/Qth2R_ghf4ISbSp7WS-ja.jpg', source: 'plus', asset_id: null },
    ]);
  });

  it('未渲染完成时返回 null', () => {
    document.body.innerHTML = '<main><section class="annotation-state"><p>正在加载 PLUS 注解</p></section></main>';
    expect(parseAnnotation(document, BASE)).toBeNull();
  });
});

describe('parsePlusId', () => {
  it('从 iframe 地址取注解 ID', () => {
    expect(parsePlusId(BASE)).toBe('6620');
    expect(parsePlusId('https://www.jin10.com/feature/x/plus/index.html#/')).toBeNull();
    expect(parsePlusId(undefined)).toBeNull();
  });
});
