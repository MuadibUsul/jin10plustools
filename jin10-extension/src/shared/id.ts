/** 客户端生成全局唯一 ID（UUID v4），离线采集也不会与服务端冲突 */
export function newId(): string {
  return crypto.randomUUID();
}
