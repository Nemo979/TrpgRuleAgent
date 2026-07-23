/**
 * 轻量进程内请求限流器。生产多实例部署时应在反向代理/网关层做共享限流；
 * 这里作为单进程 Gateway 的最后一道资源保护，不记录 URL、请求头或凭据。
 */
export class RequestRateLimiter {
  readonly #windowMs: number;
  readonly #maxRequests: number;
  readonly #buckets = new Map<string, { startedAt: number; count: number }>();

  constructor(windowMs: number, maxRequests: number) {
    this.#windowMs = windowMs;
    this.#maxRequests = maxRequests;
  }

  allow(key: string, now = Date.now()): boolean {
    const current = this.#buckets.get(key);
    if (!current || now - current.startedAt >= this.#windowMs) {
      this.#buckets.set(key, { startedAt: now, count: 1 });
      return true;
    }
    if (current.count >= this.#maxRequests) {
      return false;
    }
    current.count += 1;
    return true;
  }
}
