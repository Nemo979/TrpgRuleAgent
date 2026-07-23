import type { IncomingMessage, ServerResponse } from "node:http";
import {
  createDefaultProviderRegistry,
  createRuleAgent,
  type AgentEvent,
  type AgentMessage,
  type AgentRunContext,
  type RuleAgentConfig,
} from "@trpg-rule-agent/agent";
import { RulesClient } from "@trpg-rule-agent/rules-client";
import type { GatewayConfig } from "./config.ts";
import {
  assertNoCredentialFields,
  extractModelApiKey,
  extractSessionToken,
} from "./credentials.ts";
import { AllowlistModelEndpointPolicy, type ModelEndpointPolicy } from "./endpoint-policy.ts";
import { GatewayError } from "./errors.ts";
import { readJsonBody, sendJson } from "./http.ts";
import { parseSessionCreateRequest, type SessionConnectionConfig } from "./session-config.ts";
import type { SessionStore } from "./session-store.ts";
import { SseWriter, toPublicEvent, type PublicSource } from "./sse.ts";

/**
 * Gateway 眼中的 Agent：面向接口编程，测试可注入 fake，
 * 生产实现由 createRuleAgentFactory 基于 createRuleAgent 提供。
 */
export interface GatewayAgent {
  run(input: string, context: AgentRunContext): AsyncIterable<AgentEvent>;
  resetTurnState(): void;
  /** 当前完整消息历史（turn 成功后由 Gateway 写回会话）。 */
  messages(): AgentMessage[];
  /** 本轮引用来源（成功后作为 sources 事件发出）。 */
  sources(): PublicSource[];
}

/**
 * Agent 工厂：按会话的非敏感连接配置创建 Agent。
 * 不允许捕获全局模型配置——BYOK 下每个会话的 provider/model/baseUrl/ruleset
 * 都来自该会话自己的配置。
 */
export type AgentFactory = (
  config: SessionConnectionConfig,
  initialMessages: AgentMessage[] | undefined,
) => GatewayAgent;

export interface CreateRuleAgentFactoryOptions {
  /** 检索服务地址：唯一由服务端注入的连接项，客户端不可覆盖。 */
  retrievalBaseUrl: string;
  /** 测试可注入 RulesClient；默认按 retrievalBaseUrl 创建。 */
  client?: RulesClient;
}

/** 生产用 AgentFactory：session 配置 + 服务端 retrievalBaseUrl -> createRuleAgent。 */
export function createRuleAgentFactory(options: CreateRuleAgentFactoryOptions): AgentFactory {
  const rulesClient = options.client ?? new RulesClient(options.retrievalBaseUrl);
  return (config, initialMessages) => {
    const agentConfig: RuleAgentConfig = {
      provider: config.provider,
      model: config.model,
      baseUrl: config.baseUrl,
      retrievalBaseUrl: options.retrievalBaseUrl,
      rulesetId: config.rulesetId,
    };
    const agent = createRuleAgent({
      config: agentConfig,
      client: rulesClient,
      ...(initialMessages && initialMessages.length > 0 ? { initialMessages } : {}),
    });
    return {
      run: (input, context) => agent.run(input, context),
      resetTurnState: () => agent.resetTurnState(),
      messages: () => [...agent.runtime.messages],
      sources: () =>
        agent.citations.list().map(({ label, document }) => ({
          label,
          documentId: document.id,
          fullPath: document.fullPath,
          title: document.title,
        })),
    };
  };
}

export interface GatewayServiceDependencies {
  config: GatewayConfig;
  sessions: SessionStore;
  createAgent: AgentFactory;
  /** 可注入策略实现（契约接口）；默认按 config 构造前缀白名单实现。 */
  endpointPolicy?: ModelEndpointPolicy;
  /** 会话创建时校验 provider 的可用 ID 集合；默认取自默认 Provider Registry。 */
  providerIds?: string[];
}

/**
 * Gateway 业务编排。凭据不变量：
 * 模型 API Key 从请求头提取后只作为局部变量传入 agent.run 的
 * per-turn context，本类与 SessionStore 均不持有任何字段保存它。
 */
export class GatewayService {
  private readonly config: GatewayConfig;
  private readonly sessions: SessionStore;
  private readonly createAgent: AgentFactory;
  private readonly endpointPolicy: ModelEndpointPolicy;
  private readonly providerIds: string[];

  constructor(deps: GatewayServiceDependencies) {
    this.config = deps.config;
    this.sessions = deps.sessions;
    this.createAgent = deps.createAgent;
    this.endpointPolicy =
      deps.endpointPolicy ??
      new AllowlistModelEndpointPolicy({
        allowlist: deps.config.modelBaseUrlAllowlist,
        allowLocalhost: deps.config.allowLocalhostModel,
      });
    this.providerIds = deps.providerIds ?? createDefaultProviderRegistry().ids();
  }

  handleHealth(res: ServerResponse): void {
    sendJson(res, 200, { data: { status: "ok" } });
  }

  /**
   * POST /v1/sessions：用户提交本会话的非敏感模型连接配置。
   * - 凭据字段（含嵌套）一律 400；
   * - provider 必须已注册；rulesetId 必须在允许集合；retrievalBaseUrl 不可指定；
   * - baseUrl 在创建时即做白名单校验，尽早失败。
   */
  async handleCreateSession(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const body = await readJsonBody(req, this.config.maxBodyBytes);
    assertNoCredentialFields(body);
    const connection = parseSessionCreateRequest(body, {
      providerIds: this.providerIds,
      allowedRulesets: this.config.allowedRulesets,
      defaultRulesetId: this.config.defaultRulesetId,
    });
    this.endpointPolicy.assertAllowed(connection.baseUrl);

    const { sessionToken, expiresAt } = await this.sessions.create(connection);
    sendJson(res, 201, {
      data: { sessionToken, expiresAt: new Date(expiresAt).toISOString() },
    });
  }

  /** DELETE /v1/session：Bearer token 指定的会话被删除。 */
  async handleDeleteSession(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const token = extractSessionToken(req);
    const deleted = await this.sessions.delete(token);
    if (!deleted) {
      throw new GatewayError(404, "session_not_found", "会话不存在或已过期");
    }
    res.statusCode = 204;
    res.end();
  }

  /**
   * POST /v1/turns：SSE 响应。Agent 使用该会话创建时固定的连接配置；
   * API Key 的生命周期 = 本方法一次调用；结束后不留任何引用。
   */
  async handleTurn(req: IncomingMessage, res: ServerResponse): Promise<void> {
    const token = extractSessionToken(req);
    const apiKey = extractModelApiKey(req);

    const body = await readJsonBody(req, this.config.maxBodyBytes);
    const input = this.validateTurnBody(body);

    const snapshot = await this.sessions.touch(token);
    if (!snapshot) {
      throw new GatewayError(401, "unauthorized", "会话不存在或已过期");
    }

    // 出站边界：会话绑定的模型端点必须（仍然）命中白名单。
    this.endpointPolicy.assertAllowed(snapshot.config.baseUrl);

    const begin = await this.sessions.beginTurn(token);
    if (begin === "busy") {
      throw new GatewayError(409, "turn_in_flight", "该会话已有进行中的 turn");
    }
    if (begin === "not_found") {
      throw new GatewayError(401, "unauthorized", "会话不存在或已过期");
    }

    try {
      const agent = this.createAgent(
        snapshot.config,
        snapshot.messages.length > 0 ? snapshot.messages : undefined,
      );
      agent.resetTurnState();

      // 客户端断开 -> 取消本次 turn（终止性错误会触发 runtime 回滚）。
      const controller = new AbortController();
      res.on("close", () => {
        if (!res.writableEnded) {
          controller.abort();
        }
      });

      const sse = new SseWriter(res);
      sse.start();

      let terminalError = false;
      try {
        for await (const event of agent.run(input, { apiKey, signal: controller.signal })) {
          if (event.type === "error") {
            terminalError = true;
          }
          // 末端脱敏：基于本次 apiKey 再兜底一次，防止自定义 Provider 泄露 Key。
          sse.send(toPublicEvent(event, apiKey));
        }
      } catch {
        // Runtime 正常情况下不会向外抛异常；兜底为通用错误，不透出细节。
        terminalError = true;
        sse.send({ type: "error", error: { category: "gateway_error", message: "内部错误" } });
      }

      if (!terminalError) {
        // 只有成功 turn 才写回历史；失败 turn 已由 runtime 回滚，不污染会话。
        await this.sessions.saveMessages(token, agent.messages());
        sse.send({ type: "sources", sources: agent.sources() });
      }
      sse.send({ type: "done" });
      sse.end();
    } finally {
      await this.sessions.endTurn(token);
    }
  }

  /** turn body 只允许 { input: string }，其余字段（含凭据）一律 400。 */
  private validateTurnBody(body: Record<string, unknown>): string {
    assertNoCredentialFields(body);
    const keys = Object.keys(body);
    const unknown = keys.filter((key) => key !== "input");
    if (unknown.length > 0) {
      throw new GatewayError(400, "invalid_request", `不支持的字段：${unknown.join("、")}`);
    }
    const input = body.input;
    if (typeof input !== "string" || input.trim() === "") {
      throw new GatewayError(400, "invalid_request", "input 必须是非空字符串");
    }
    if (input.length > this.config.maxInputChars) {
      throw new GatewayError(
        400,
        "input_too_long",
        `input 超过 ${this.config.maxInputChars} 字符上限`,
      );
    }
    return input;
  }
}
