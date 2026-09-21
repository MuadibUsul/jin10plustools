import type { SectionId } from './section';

export type MaterialSource = 'jin10';
export type CollectMethod = 'context_menu' | 'page_scan';
export type AiStatus = 'pending' | 'analyzing' | 'done' | 'failed';
export type SyncStatus = 'local' | 'queued' | 'synced' | 'conflict';

/**
 * PLUS 解读抓取状态
 * - none：快讯没有 PLUS 注解链接
 * - pending：有链接，尚未抓取
 * - fetched：已抓取
 * - no_permission：未登录或无会员权限（401/403）
 * - failed：其他失败，可重试
 * - manual：编辑手动填写
 */
export type InterpretationStatus =
  | 'none'
  | 'pending'
  | 'fetched'
  | 'no_permission'
  | 'failed'
  | 'manual';

export type ImageSource = 'flash' | 'plus' | 'upload';

/**
 * 金十条目类型
 * - flash：普通快讯
 * - vip：VIP 快讯（未解锁时 locked=true，正文只有标题）
 * - article：文章卡片（标题 + 摘要 + 详情链接）
 */
export type ItemKind = 'flash' | 'vip' | 'article';

export interface MaterialImage {
  /** 原图地址（已去掉 /ncover、/lite 等缩略图后缀） */
  url: string;
  source: ImageSource;
  /** 本地已下载的图片文件 ID（见 AssetStore），下载失败为 null */
  asset_id: string | null;
}

/** 已下载的图片文件 */
export interface ImageAsset {
  id: string;
  source_url: string;
  mime: string;
  size: number;
  /** data:image/...;base64,... */
  data_url: string;
  created_at: string;
}

/** 注解模块：view = 「PLUS观点」卡片；observation = 「重点观察」编号列表 */
export type PlusModuleKind = 'view' | 'observation' | 'unknown';

export interface PlusModule {
  kind: PlusModuleKind;
  /** 模块标题，如「PLUS观点」「重点观察」 */
  heading: string | null;
  paragraphs: string[];
  images: MaterialImage[];
}

/** PLUS 注解正文（来自 iframe 内 .annotation-content 的渲染结果），即 PPT 中的【解读】 */
export interface PlusInterpretation {
  /** 注解 ID（iframe 地址 #/?id=6618） */
  plus_id: string | null;
  title: string;
  modules: PlusModule[];
  fetched_at: string;
}

/**
 * 解析器产物：只包含从金十页面直接能拿到的字段。
 * 由 content script 生成，交给 background 补全为 NewsMaterial。
 */
export interface NewsMaterialDraft {
  source: MaterialSource;
  /** 金十条目 ID（.jin-flash-item-container 的 id，形如 flash20260921170417801800） */
  source_item_id: string | null;
  item_kind: ItemKind;
  /** 内容被会员锁定（未解锁 VIP），正文不完整 */
  locked: boolean;
  /** 金十标签，如「市场要闻」「VIP」 */
  tags: string[];
  /** 文章详情页链接（仅 article） */
  detail_url: string | null;
  /** 展示标题：PLUS 标题 > 快讯标题 > 正文首句 */
  title: string;
  /** 快讯标题 .right-common-title */
  flash_title: string | null;
  /** 清洗后的快讯正文（已去掉「金十数据X月X日讯，」前缀），即 PPT 中的【新闻】 */
  content: string;
  /** .flash-text 原文 */
  content_raw: string;
  /** PLUS 注解标题 .plus-link-title */
  plus_title: string | null;
  has_plus_link: boolean;
  images: MaterialImage[];
  /** 快讯发布时间（北京时间 ISO），无法确定为 null */
  publish_time: string | null;
  url: string;
  /** 清洗后的组件 HTML（去脚本/样式/注释/Vue scope 属性） */
  raw_html: string;
}

/** 素材完整对象。服务端为权威数据源，本地存储仅做缓存与离线队列。 */
export interface NewsMaterial extends NewsMaterialDraft {
  id: string;
  collect_time: string;
  collect_method: CollectMethod;
  /** sha256(标准化标题 + 正文)，团队内去重 */
  fingerprint: string;

  interpretation_status: InterpretationStatus;
  interpretation: PlusInterpretation | null;
  /** 通过「加入并 AI 分析」采集，服务端接入后优先分析 */
  analyze_requested: boolean;

  category: SectionId | null;
  importance_score: number;
  final_score: number;
  level: 'S' | 'A' | 'B' | 'C' | null;
  ai_status: AiStatus;
  ai_reason: string | null;
  ai_error: string | null;

  collector_id: string | null;
  collected_by: string[];
  team_id: string | null;
  sync_status: SyncStatus;
  updated_at: string;
  deleted_at: string | null;
  schema_version: 1;
}
