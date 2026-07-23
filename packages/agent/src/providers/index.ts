import { ProviderRegistry } from "../core/provider.ts";
import { OpenAICompatibleProvider } from "./openai-compatible.ts";

export { OpenAICompatibleProvider } from "./openai-compatible.ts";
export { parseSseStream } from "./sse.ts";

/**
 * 默认 Provider Registry。首版只注册 openai-compatible，
 * 未来的 Provider（如 Anthropic、Gemini）在这里增加注册项。
 */
export function createDefaultProviderRegistry(): ProviderRegistry {
  const registry = new ProviderRegistry();
  registry.register("openai-compatible", () => new OpenAICompatibleProvider());
  return registry;
}
