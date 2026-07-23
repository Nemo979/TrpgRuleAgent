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

export class RulesClient implements RulesProvider {
  readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
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
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...init.headers,
      },
    });

    const body = (await response.json()) as { data?: T; error?: string };
    if (!response.ok || body.data === undefined) {
      throw new RulesClientError(body.error ?? `Request failed: ${response.status}`, response.status);
    }
    return body.data;
  }
}
