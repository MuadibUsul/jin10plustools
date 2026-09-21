import { BEIJING_OFFSET, BEIJING_TZ } from './constants';

export interface DateParts {
  year: number;
  month: number;
  day: number;
}

const partsFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: BEIJING_TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

export function nowIso(): string {
  return new Date().toISOString();
}

export function beijingDateParts(date: Date = new Date()): DateParts {
  const parts = partsFormatter.formatToParts(date);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return { year: get('year'), month: get('month'), day: get('day') };
}

const pad = (n: number) => String(n).padStart(2, '0');

/** 北京时间日期键 YYYY-MM-DD，用于「今日素材」统计 */
export function beijingDayKey(date: Date = new Date()): string {
  const { year, month, day } = beijingDateParts(date);
  return `${year}-${pad(month)}-${pad(day)}`;
}

/**
 * 页面只给出「月/日」时推断年份：
 * 以当前北京日期为基准，若推断结果比现在晚超过 2 天（如 1 月初看到 12 月 31 日的快讯），归为上一年。
 */
export function inferYear(month: number, day: number, now: Date = new Date()): number {
  const today = beijingDateParts(now);
  const candidate = Date.UTC(today.year, month - 1, day);
  const todayUtc = Date.UTC(today.year, today.month - 1, today.day);
  const twoDays = 2 * 24 * 60 * 60 * 1000;
  return candidate - todayUtc > twoDays ? today.year - 1 : today.year;
}

/** 组合北京时间 ISO 字符串；time 形如 HH:MM 或 HH:MM:SS */
export function toBeijingIso(date: DateParts, time: string): string | null {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(time.trim());
  if (!m) return null;
  const [, hh = '0', mm = '0', ss = '0'] = m;
  return `${date.year}-${pad(date.month)}-${pad(date.day)}T${pad(Number(hh))}:${mm}:${pad(Number(ss))}${BEIJING_OFFSET}`;
}
