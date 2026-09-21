import { describe, expect, it } from 'vitest';
import {
  firstSentence,
  parseItemIdTime,
  parsePrefixDate,
  stripJin10Prefix,
  stripReadMore,
} from '@/content/parser/normalize';
import { materialFingerprint } from '@/shared/hash';
import { isMessage } from '@/shared/messaging';
import { beijingDayKey, inferYear, toBeijingIso } from '@/shared/time';

describe('normalize', () => {
  it('解析并去掉快讯前缀的多种写法', () => {
    expect(stripJin10Prefix('金十数据9月21日讯，内容')).toBe('内容');
    expect(stripJin10Prefix('金十数据 12 月 1 日讯：内容')).toBe('内容');
    expect(stripJin10Prefix('没有前缀')).toBe('没有前缀');
    expect(parsePrefixDate('金十数据9月21日讯，x')).toEqual({ month: 9, day: 21 });
    expect(parsePrefixDate('x')).toBeNull();
  });

  it('条目 ID 解析时间', () => {
    expect(parseItemIdTime('flash20260921170417801800')).toBe('2026-09-21T17:04:17+08:00');
    expect(parseItemIdTime('flash123')).toBeNull();
    expect(parseItemIdTime(null)).toBeNull();
  });

  it('去掉文章摘要的「点击查看...」', () => {
    expect(stripReadMore('驶向美国……点击查看...')).toBe('驶向美国');
    expect(stripReadMore('正常结尾。')).toBe('正常结尾。');
  });

  it('首句截断', () => {
    expect(firstSentence('第一句。第二句。')).toBe('第一句');
    expect(firstSentence('很'.repeat(50), 10)).toBe(`${'很'.repeat(10)}…`);
  });
});

describe('time', () => {
  it('按北京时间划分日期', () => {
    // UTC 16:30 = 北京时间次日 00:30
    expect(beijingDayKey(new Date('2026-09-20T16:30:00Z'))).toBe('2026-09-21');
  });

  it('跨年时推断为上一年', () => {
    const jan2 = new Date('2027-01-02T02:00:00Z');
    expect(inferYear(12, 31, jan2)).toBe(2026);
    expect(inferYear(1, 2, jan2)).toBe(2027);
  });

  it('组合 ISO 时间', () => {
    expect(toBeijingIso({ year: 2026, month: 9, day: 1 }, '9:05')).toBe('2026-09-01T09:05:00+08:00');
    expect(toBeijingIso({ year: 2026, month: 9, day: 1 }, 'bad')).toBeNull();
  });
});

describe('fingerprint', () => {
  it('忽略空白与标点差异', async () => {
    const a = await materialFingerprint('标题', '美元走高，涨0.2%。');
    const b = await materialFingerprint('标题 ', '美元走高 涨0.2%');
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
  });
});

describe('messaging', () => {
  it('识别合法消息', () => {
    expect(isMessage({ type: 'PING', payload: undefined })).toBe(true);
    expect(isMessage('PING')).toBe(false);
    expect(isMessage(null)).toBe(false);
  });
});
