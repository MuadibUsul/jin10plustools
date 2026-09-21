export const JIN10_ORIGIN = 'https://www.jin10.com';
export const JIN10_URL_PATTERN = `${JIN10_ORIGIN}/*`;

export const SCHEMA_VERSION = 1 as const;

/** 北京时间，用于「今日」划分与快讯时间解析 */
export const BEIJING_TZ = 'Asia/Shanghai';
export const BEIJING_OFFSET = '+08:00';

/** chrome.storage.local 键 */
export const STORAGE_KEYS = {
  /** 素材 ID 列表，按采集时间倒序 */
  materialIndex: 'materials:index',
  /** 去重索引：item:<金十条目ID> / fp:<指纹> → 素材 ID */
  materialDedupe: 'materials:dedupe',
  material: (id: string) => `material:${id}`,
  asset: (id: string) => `asset:${id}`,
  authSession: 'auth:session',
  lastApiBaseUrl: 'settings:lastApiBaseUrl',
  syncState: 'sync:state',
} as const;

export const STORAGE_PREFIX = { material: 'material:', asset: 'asset:' } as const;

/** raw_html 保存上限（字符），防止异常大节点撑满存储 */
export const MAX_RAW_HTML_LENGTH = 100_000;

export function isJin10Url(url: string | undefined): boolean {
  return typeof url === 'string' && url.startsWith(`${JIN10_ORIGIN}/`);
}
