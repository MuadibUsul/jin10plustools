export interface CaptureShortcut {
  primary: boolean;
  alt: boolean;
  shift: boolean;
}

export const DEFAULT_CAPTURE_SHORTCUT: CaptureShortcut = {
  primary: true,
  alt: false,
  shift: false,
};

export function matchesCaptureClick(event: MouseEvent, shortcut: CaptureShortcut): boolean {
  const primaryPressed = /Mac/i.test(navigator.platform) ? event.metaKey : event.ctrlKey;
  return (
    event.button === 0 &&
    primaryPressed === shortcut.primary &&
    (shortcut.primary ? event.ctrlKey !== event.metaKey : !event.ctrlKey && !event.metaKey) &&
    event.altKey === shortcut.alt &&
    event.shiftKey === shortcut.shift
  );
}

export function formatCaptureShortcut(shortcut: CaptureShortcut): string {
  const primary = /Mac/i.test(navigator.platform) ? '⌘' : 'Ctrl';
  return [shortcut.primary ? primary : '', shortcut.alt ? 'Alt' : '', shortcut.shift ? 'Shift' : '', '左键']
    .filter(Boolean)
    .join(' + ');
}
