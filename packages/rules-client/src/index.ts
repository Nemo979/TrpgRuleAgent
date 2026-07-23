import type {
  HealthResponse,
  ReadRulesRequest,
  RuleDocument,
  RuleSearchHit,
  RuleSearchRequest,
  RuleSource,
} from "@trpg-rule-agent/rules-types";

/**
 * 检索能力抽象：本地 Python 服务、云端向量服务或测试 fake 都实现同一契约。
 * Gateway/Agent 不感知具体部署位置。
 */
export interface RulesProvider {
  health(signal?: AbortSignal): Promise<HealthResponse>;
  search(input: RuleSearchRequest, signal?: AbortSignal): Promise<RuleSearchHit[]>;
  read(input: ReadRulesRequest, signal?: AbortSignal): Promise<RuleDocument[]>;
  sources(rulesetId: string, signal?: AbortSignal): Promise<RuleSource[]>;
}

export class RulesClientError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "RulesClientError";
    this.status = status;
  }
}

export interface RulesClientOptions {
  /** 用于云端检索服务的服务端鉴权头；不会暴露给浏览器。 */
  headers?: Record<string, string>;
  /** 请求超时（毫秒）；默认 15 秒。 */
  timeoutMs?: number;
  /** 测试或运行时适配器可注入 fetch，默认使用全局 fetch。 */
  fetch?: typeof fetch;
}

export class RulesClient implements RulesProvider {
  readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly timeoutMs: number;
  private readonly fetchImpl: typeof fetch;

  constructor(baseUrl: string, options: RulesClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.headers = { ...options.headers };
    this.timeoutMs = options.timeoutMs ?? 15_000;
    this.fetchImpl = options.fetch ?? fetch;
    if (!Number.isInteger(this.timeoutMs) || this.timeoutMs <= 0) {
      throw new RangeError("RulesClient timeoutMs must be a positive integer");
    }
  }

  health(signal?: AbortSignal): Promise<HealthResponse> {
    return this.request<HealthResponse>("/health", {
      method: "GET",
      ...(signal ? { signal } : {}),
    });
  }

  search(input: RuleSearchRequest, signal?: AbortSignal): Promise<RuleSearchHit[]> {
    return this.request<RuleSearchHit[]>("/search", {
      method: "POST",
      body: JSON.stringify(input),
      ...(signal ? { signal } : {}),
    });
  }

  read(input: ReadRulesRequest, signal?: AbortSignal): Promise<RuleDocument[]> {
    return this.request<RuleDocument[]>("/documents/read", {
      method: "POST",
      body: JSON.stringify(input),
      ...(signal ? { signal } : {}),
    });
  }

  sources(rulesetId: string, signal?: AbortSignal): Promise<RuleSource[]> {
    const query = new URLSearchParams({ rulesetId });
    return this.request<RuleSource[]>(`/sources?${query.toString()}`, {
      method: "GET",
      ...(signal ? { signal } : {}),
    });
  }

  private async request<T>(path: string, init: RequestInit): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const externalSignal = init.signal;
    const abort = (): void => controller.abort();
    externalSignal?.addEventListener("abort", abort, { once: true });
    try {
      const response = await this.fetchImpl(`${this.baseUrl}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          "content-type": "application/json",
          ...this.headers,
          ...init.headers,
        },
      });
      let body: { data?: T; error?: string };
      try {
        body = (await response.json()) as { data?: T; error?: string };
      } catch {
        throw new RulesClientError("检索服务返回了无效响应", response.status);
      }
      if (!response.ok || body.data === undefined) {
        throw new RulesClientError(body.error ?? `Request failed: ${response.status}`, response.status);
      }
      return body.data;
    } catch (error) {
      if (error instanceof RulesClientError) {
        throw error;
      }
      if (externalSignal?.aborted) {
        throw new RulesClientError("检索请求已取消", 499);
      }
      if (controller.signal.aborted) {
        throw new RulesClientError("检索服务请求超时", 504);
      }
      throw new RulesClientError("检索服务不可用", 503);
    } finally {
      clearTimeout(timer);
      externalSignal?.removeEventListener("abort", abort);
    }
  }
}
