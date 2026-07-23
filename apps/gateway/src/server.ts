import { createServer, type Server } from "node:http";
import { assertNoCredentialQuery } from "./credentials.ts";
import { GatewayError } from "./errors.ts";
import { applySecurityHeaders, handleCors, sendError } from "./http.ts";
import type { GatewayService } from "./service.ts";

export interface CreateGatewayServerOptions {
  service: GatewayService;
  allowedOrigins: string[];
}

/**
 * 纯路由层：解析 URL、分发到 GatewayService、统一错误映射。
 * 未知异常一律 500 且不透出内部细节（避免 stack/凭据泄露）。
 */
export function createGatewayServer(options: CreateGatewayServerOptions): Server {
  const { service, allowedOrigins } = options;

  return createServer((req, res) => {
    void (async () => {
      applySecurityHeaders(res);
      if (handleCors(req, res, allowedOrigins)) {
        return;
      }

      try {
        const url = new URL(req.url ?? "/", "http://gateway.internal");
        const route = `${req.method ?? "GET"} ${url.pathname}`;

        if (url.pathname.startsWith("/v1/")) {
          assertNoCredentialQuery(url);
        }

        switch (route) {
          case "GET /health":
            service.handleHealth(res);
            return;
          case "POST /v1/sessions":
            await service.handleCreateSession(req, res);
            return;
          case "POST /v1/turns":
            await service.handleTurn(req, res);
            return;
          case "DELETE /v1/session":
            await service.handleDeleteSession(req, res);
            return;
          default:
            throw new GatewayError(404, "not_found", "接口不存在");
        }
      } catch (error) {
        if (res.headersSent) {
          // SSE 已开始：只能直接结束连接。
          res.end();
          return;
        }
        if (error instanceof GatewayError) {
          sendError(res, error);
          return;
        }
        sendError(res, new GatewayError(500, "internal_error", "内部错误"));
      }
    })();
  });
}
