import type {
  Message,
  MessageRequest,
  MessageResponse,
  MessageType,
  Result,
} from '@/types/messages';
import { toAppError } from './errors';

export type MessageHandler<K extends MessageType> = (
  payload: MessageRequest<K>,
  sender: chrome.runtime.MessageSender,
) => MessageResponse<K> | Promise<MessageResponse<K>>;

export type HandlerMap = { [K in MessageType]?: MessageHandler<K> };

export function isMessage(value: unknown): value is Message {
  return (
    typeof value === 'object' &&
    value !== null &&
    'type' in value &&
    typeof (value as { type: unknown }).type === 'string'
  );
}

/**
 * 创建 chrome.runtime.onMessage 监听器。
 * 只响应 handlers 中登记的类型，其余消息交给其他监听者（返回 false）。
 */
export function createMessageListener(handlers: HandlerMap) {
  return (
    message: unknown,
    sender: chrome.runtime.MessageSender,
    sendResponse: (response: Result<unknown>) => void,
  ): boolean => {
    if (!isMessage(message)) return false;
    const handler = handlers[message.type] as MessageHandler<MessageType> | undefined;
    if (!handler) return false;

    Promise.resolve()
      .then(() => handler(message.payload as never, sender))
      .then(
        (data) => sendResponse({ ok: true, data }),
        (err: unknown) => sendResponse({ ok: false, error: toAppError(err) }),
      );
    return true; // 保持通道，异步回复
  };
}

function toMessage<K extends MessageType>(type: K, payload: MessageRequest<K>): Message<K> {
  return { type, payload } as Message<K>;
}

/** 发送到 background（或其他扩展页面） */
export async function sendToBackground<K extends MessageType>(
  type: K,
  payload: MessageRequest<K>,
): Promise<Result<MessageResponse<K>>> {
  try {
    const res = (await chrome.runtime.sendMessage(toMessage(type, payload))) as
      | Result<MessageResponse<K>>
      | undefined;
    return res ?? { ok: false, error: { code: 'NO_RECEIVER', message: '后台未响应' } };
  } catch (err) {
    return { ok: false, error: toAppError(err) };
  }
}

/** 发送到指定标签页的 content script（仅顶层 frame） */
export async function sendToTab<K extends MessageType>(
  tabId: number,
  type: K,
  payload: MessageRequest<K>,
): Promise<Result<MessageResponse<K>>> {
  try {
    const res = (await chrome.tabs.sendMessage(tabId, toMessage(type, payload), {
      frameId: 0,
    })) as Result<MessageResponse<K>> | undefined;
    return res ?? { ok: false, error: { code: 'NO_RECEIVER', message: '页面未响应' } };
  } catch (err) {
    return { ok: false, error: toAppError(err) };
  }
}
