export const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** 轮询直到 check 返回非空值；超时返回 null */
export async function waitFor<T>(
  check: () => T | null | undefined,
  { timeout = 5000, interval = 100 }: { timeout?: number; interval?: number } = {},
): Promise<T | null> {
  const deadline = Date.now() + timeout;
  for (;;) {
    const value = check();
    if (value !== null && value !== undefined) return value;
    if (Date.now() >= deadline) return null;
    await sleep(interval);
  }
}

/** 串行执行：同一时刻只运行一个任务，后来的排队等待 */
export function createSerialQueue() {
  let tail: Promise<unknown> = Promise.resolve();
  return <T>(task: () => Promise<T>): Promise<T> => {
    const run = tail.then(task, task);
    tail = run.catch(() => undefined);
    return run;
  };
}
