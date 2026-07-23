/**
 * 协议类型再导出（apps/web/src/types.ts）。
 *
 * 单一事实来源：直接从共享 SDK @trpg-rule-agent/gateway-client 再导出
 * 线协议类型，避免 apps/web 与 SDK 之间出现类型漂移。字段含义见
 * docs/gateway-api.md。运行时不引入任何 SDK 代码（纯 type 再导出）。
 */

export type {
  GatewayWireError,
  GatewaySource,
  GatewayEvent,
  GatewaySessionStatus,
  SessionModelConfig,
  SessionCreateOptions,
} from "@trpg-rule-agent/gateway-client";
