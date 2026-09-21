/** 页面右上角提示，挂在 Shadow DOM 中，不受金十页面样式影响，也不污染页面 */

export type ToastTone = 'info' | 'success' | 'warning' | 'error';

const TONE_COLORS: Record<ToastTone, string> = {
  info: '#1fa2e8',
  success: '#10b981',
  warning: '#f59e0b',
  error: '#ef4444',
};

const STYLE = `
  :host { all: initial; }
  .stack { position: fixed; top: 72px; right: 24px; z-index: 2147483647;
    display: flex; flex-direction: column; gap: 8px; pointer-events: none;
    font-family: 'Source Han Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif; }
  .toast { min-width: 240px; max-width: 360px; padding: 10px 14px 10px 12px; border-radius: 10px;
    background: #fff; color: #0d2b45; font-size: 13px; line-height: 1.5;
    box-shadow: 0 6px 24px rgba(13, 43, 69, .18); display: flex; gap: 10px; align-items: flex-start;
    transition: opacity .2s, transform .2s; }
  .toast.hide { opacity: 0; transform: translateY(-6px); }
  .dot { width: 8px; height: 8px; border-radius: 50%; margin-top: 6px; flex-shrink: 0; }
  .brand { font-weight: 600; margin-right: 4px; }
  .detail { display: block; color: #64748b; font-size: 12px; }
`;

let stack: HTMLElement | null = null;

function getStack(): HTMLElement {
  if (stack?.isConnected) return stack;
  const host = document.createElement('plus-research-toast');
  const shadow = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = STYLE;
  stack = document.createElement('div');
  stack.className = 'stack';
  shadow.append(style, stack);
  document.documentElement.append(host);
  return stack;
}

export interface ToastHandle {
  update(tone: ToastTone, message: string, detail?: string): void;
  dismiss(delayMs?: number): void;
}

export function showToast(tone: ToastTone, message: string, detail?: string): ToastHandle {
  const el = document.createElement('div');
  el.className = 'toast';
  const dot = document.createElement('span');
  dot.className = 'dot';
  const body = document.createElement('div');
  el.append(dot, body);
  getStack().append(el);

  let timer: ReturnType<typeof setTimeout> | undefined;
  const handle: ToastHandle = {
    update(nextTone, nextMessage, nextDetail) {
      dot.style.background = TONE_COLORS[nextTone];
      body.replaceChildren();
      const brand = document.createElement('span');
      brand.className = 'brand';
      brand.textContent = 'PLUS';
      body.append(brand, nextMessage);
      if (nextDetail) {
        const d = document.createElement('span');
        d.className = 'detail';
        d.textContent = nextDetail;
        body.append(d);
      }
    },
    dismiss(delayMs = 2800) {
      clearTimeout(timer);
      timer = setTimeout(() => {
        el.classList.add('hide');
        setTimeout(() => el.remove(), 250);
      }, delayMs);
    },
  };
  handle.update(tone, message, detail);
  return handle;
}
