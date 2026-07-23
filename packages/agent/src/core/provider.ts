import { AgentError } from "./errors.ts";
import type { AgentMessage, Usage } from "./messages.ts";
import type { ToolSpec } from "./tools.ts";

export interface ModelConfig {
  id: string;
  contextWindow?: number;
  maxTokens?: number;
  reasoning?: boolean;
}

export type ModelFinishReason = "stop" | "tool_calls" | "length" | "other";

/**
 * Provider 产出的内部事件。Agent Loop 只消费这些事件，
 * 不依赖任何厂商数据结构。
 */
export type ModelEvent =
  | { type: "text_delta"; delta: string }
  | { type: "tool_call_start"; index: number; id: string; name: string }
  | { type: "tool_call_arguments_delta"; index: number; delta: string }
  | { type: "tool_call_end"; index: number; id: string; name: string; arguments: string }
  | { type: "usage"; usage: Usage }
  | { type: "finish"; reason: ModelFinishReason };

export interface ModelRequest {
  model: ModelConfig;
  messages: AgentMessage[];
  tools: ToolSpec[];
}

/**
 * 执行上下文：API Key 只经由这里传入 Provider，
 * 不写入消息、事件、日志或错误文本。
 */
export interface ProviderContext {
  baseUrl: string;
  apiKey: string;
  signal?: AbortSignal;
}

export interface ModelProvider {
  readonly id: string;
  stream(request: ModelRequest, context: ProviderContext): AsyncIterable<ModelEvent>;
}

export type ModelProviderFactory = () => ModelProvider;

export class ProviderRegistry {
  private readonly factories = new Map<string, ModelProviderFactory>();

  register(id: string, factory: ModelProviderFactory): void {
    this.factories.set(id, factory);
  }

  has(id: string): boolean {
    return this.factories.has(id);
  }

  ids(): string[] {
    return [...this.factories.keys()];
  }

  create(id: string): ModelProvider {
    const factory = this.factories.get(id);
    if (!factory) {
      throw new AgentError(
        "configuration_error",
        `未注册的模型 Provider：${id}。可用值：${this.ids().join("、") || "(无)"}`,
      );
    }
    return factory();
  }
}
