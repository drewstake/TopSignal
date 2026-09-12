export class BotRequestTimeout extends Error {
  constructor(message: string) {
    super(message);
    this.name = "BotRequestTimeout";
  }
}

/** Bound reads even when an underlying adapter does not settle after abort. */
export async function boundedBotRequest<T>(
  request: Promise<T>,
  controller: AbortController,
  message: string,
  timeoutMs = 60_000,
): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abort: () => void = () => {};
  const deadline = new Promise<never>((_, reject) => {
    abort = () => reject(new DOMException("Request cancelled", "AbortError"));
    if (controller.signal.aborted) {
      abort();
      return;
    }
    controller.signal.addEventListener("abort", abort, { once: true });
    timer = setTimeout(() => {
      reject(new BotRequestTimeout(message));
      controller.abort();
    }, timeoutMs);
  });
  try {
    return await Promise.race([request, deadline]);
  } finally {
    clearTimeout(timer);
    controller.signal.removeEventListener("abort", abort);
  }
}
