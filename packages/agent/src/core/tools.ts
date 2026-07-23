import { AgentError } from "./errors.ts";
import type { JsonSchema } from "./schema.ts";

export interface ToolExecutionContext {
  toolCallId: string;
  signal?: AbortSignal;
}

export interface ToolResultContent {
  type: "text";
  text: string;
}

export interface ToolResult {
  content: ToolResultContent[];
  details?: unknown;
}

/**
 * 业务无关的工具定义。parameters 为普通 JSON Schema 对象，
 * Runtime 在执行前用内置校验器校验参数。
 */
export interface ToolDefinition<TParams = unknown> {
  name: string;
  label?: string;
  description: string;
  parameters: JsonSchema;
  execute(params: TParams, context: ToolExecutionContext): Promise<ToolResult>;
}

/** 提供给 ModelProvider 的工具声明。 */
export interface ToolSpec {
  name: string;
  description: string;
  parameters: JsonSchema;
}

export class ToolRegistry {
  private readonly tools = new Map<string, ToolDefinition>();

  register(tool: ToolDefinition<never>): void {
    if (this.tools.has(tool.name)) {
      throw new AgentError("configuration_error", `工具重复注册：${tool.name}`);
    }
    this.tools.set(tool.name, tool as ToolDefinition);
  }

  get(name: string): ToolDefinition | undefined {
    return this.tools.get(name);
  }

  list(): ToolDefinition[] {
    return [...this.tools.values()];
  }

  toolSpecs(): ToolSpec[] {
    return this.list().map((tool) => ({
      name: tool.name,
      description: tool.description,
      parameters: tool.parameters,
    }));
  }
}

export function toolResultText(result: ToolResult): string {
  return result.content.map((part) => part.text).join("\n");
}
