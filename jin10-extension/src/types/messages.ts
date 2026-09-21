import type { AppError } from '@/shared/errors';
import type { AuthSession, SyncReport } from './settings';
import type {
  CollectMethod,
  ImageSource,
  InterpretationStatus,
  NewsMaterial,
  NewsMaterialDraft,
  PlusInterpretation,
} from './material';

export type RuntimeContext = 'background' | 'content';

export interface PingResponse {
  context: RuntimeContext;
  version: string;
  at: string;
}

export interface ScanFailure {
  index: number;
  reason: string;
}

export interface ScanResponse {
  total: number;
  drafts: NewsMaterialDraft[];
  failures: ScanFailure[];
}

/** content script 已下载的图片，交给 background 存入 AssetStore */
export interface ImageUpload {
  url: string;
  source: ImageSource;
  mime: string;
  size: number;
  data_url: string;
}

export interface SaveMaterialRequest {
  draft: NewsMaterialDraft;
  interpretation: PlusInterpretation | null;
  interpretation_status: InterpretationStatus;
  interpretation_error: string | null;
  images: ImageUpload[];
  method: CollectMethod;
  analyze: boolean;
}

/**
 * created：新素材
 * updated：已存在，本次补充了解读或图片
 * duplicate：已存在，没有新内容
 */
export type SaveOutcome = 'created' | 'updated' | 'duplicate';

export interface SaveMaterialResponse {
  outcome: SaveOutcome;
  material: NewsMaterial;
}

export interface CollectResponse {
  save: SaveMaterialResponse;
  interpretation_status: InterpretationStatus;
  interpretation_error: string | null;
  images_saved: number;
  images_total: number;
}

/**
 * 消息协议：键为消息类型，req 为载荷，res 为成功时的返回数据。
 * 新增消息只需在此登记，发送与处理两端即获得类型检查。
 */
export interface MessageMap {
  PING: { req: undefined; res: PingResponse };
  /** content：扫描当前金十页面并返回解析结果（不入库） */
  SCAN_PAGE: { req: undefined; res: ScanResponse };
  /** content：采集最近一次右键位置所在的快讯 */
  COLLECT_AT_CONTEXT: { req: { analyze: boolean }; res: CollectResponse };
  /** background：保存素材（去重、存图） */
  SAVE_MATERIAL: { req: SaveMaterialRequest; res: SaveMaterialResponse };
  /** background：删除素材 */
  DELETE_MATERIAL: { req: { id: string }; res: { deleted: boolean } };
  /** background：登录团队服务端（调用前需已获得该地址的主机权限） */
  LOGIN: { req: { api_base_url: string; email: string; password: string }; res: AuthSession };
  LOGOUT: { req: undefined; res: undefined };
  /** background：立即与团队服务端同步 */
  SYNC_NOW: { req: undefined; res: SyncReport };
}

export type MessageType = keyof MessageMap;
export type MessageRequest<K extends MessageType> = MessageMap[K]['req'];
export type MessageResponse<K extends MessageType> = MessageMap[K]['res'];

export type Message<K extends MessageType = MessageType> = {
  [P in K]: { type: P; payload: MessageRequest<P> };
}[K];

export type Result<T> = { ok: true; data: T } | { ok: false; error: AppError };
