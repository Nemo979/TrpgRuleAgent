import { loadGatewayConfig } from "./config.ts";
import { createGatewayServer } from "./server.ts";
import { GatewayService, createRuleAgentFactory } from "./service.ts";
import { InMemorySessionStore } from "./session-store.ts";

/**
 * BYOK Gateway 入口。
 * 凭据模型：服务端不加载、不存储任何模型 API Key；
 * Key 由前端页面内存持有，每次 turn 通过 X-Model-Api-Key 头传入。
 * 模型连接配置（provider/model/baseUrl/rulesetId）由用户在创建会话时提交，
 * 服务端只注入 retrievalBaseUrl 并执行白名单校验。
 */
function main(): void {
  const config = loadGatewayConfig(process.env);

  const service = new GatewayService({
    config,
    sessions: new InMemorySessionStore({ ttlMs: config.sessionTtlMs }),
    createAgent: createRuleAgentFactory({ retrievalBaseUrl: config.retrievalBaseUrl }),
  });

  const server = createGatewayServer({
    service,
    allowedOrigins: config.allowedOrigins,
  });

  server.listen(config.port, config.host, () => {
    console.log(`[gateway] listening on http://${config.host}:${config.port}`);
    console.log(`[gateway] retrieval endpoint: ${config.retrievalBaseUrl}`);
    console.log(`[gateway] allowed rulesets: ${config.allowedRulesets.join(", ")}`);
    console.log(
      `[gateway] model allowlist: ${config.modelBaseUrlAllowlist.join(", ") || "(empty)"}` +
        (config.allowLocalhostModel ? " + localhost(dev)" : ""),
    );
  });

  const shutdown = (): void => {
    server.close(() => process.exit(0));
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

main();
