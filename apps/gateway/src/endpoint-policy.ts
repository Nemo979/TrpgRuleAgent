import { GatewayError } from "./errors.ts";

/**
 * 模型端点出站策略契约（SSRF 边界）。
 * GatewayService 面向此接口编程：测试可注入 fake，
 * 未来多租户可注入按租户解析白名单的实现。
 */
export interface ModelEndpointPolicy {
  /** 校验失败时抛出 GatewayError(400/403)，通过则静默返回。 */
  assertAllowed(baseUrl: string): void;
}

export interface AllowlistModelEndpointPolicyOptions {
  /**
   * 允许的模型 Base URL 前缀白名单（如 https://api.openai.com/v1）。
   * 匹配 origin + 路径边界：条目 https://host/allowed 放行
   * https://host/allowed 与 https://host/allowed/v1，
   * 但不放行 https://host/allowedX 或 https://host/admin。
   * 条目只有 origin（路径为 /）时放行该 origin 下所有路径。
   */
  allowlist: string[];
  /** 本地开发开关：允许 http/https 的 localhost、127.0.0.1、[::1]，默认关闭。 */
  allowLocalhost?: boolean;
}

const LOCAL_HOSTNAMES = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

function isLocalHostname(hostname: string): boolean {
  return LOCAL_HOSTNAMES.has(hostname);
}

interface NormalizedPrefix {
  origin: string;
  /** 去掉尾部斜杠的路径；origin-only 条目为 ""。 */
  path: string;
}

/** 解析 URL 并套用共同约束：绝对 URL、无 userinfo/query/fragment。 */
function parseStrict(raw: string, subject: string, errorStatus: 400 | 500): URL {
  const code = errorStatus === 500 ? "configuration_error" : "invalid_model_base_url";
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new GatewayError(errorStatus, code, `${subject}不是合法的绝对 URL：${raw}`);
  }
  if (url.username !== "" || url.password !== "") {
    throw new GatewayError(errorStatus, code, `${subject}不允许包含 userinfo`);
  }
  if (url.search !== "") {
    throw new GatewayError(errorStatus, code, `${subject}不允许包含 query`);
  }
  if (url.hash !== "") {
    throw new GatewayError(errorStatus, code, `${subject}不允许包含 fragment`);
  }
  return url;
}

function normalizePath(pathname: string): string {
  const stripped = pathname.replace(/\/+$/, "");
  return stripped === "" ? "" : stripped;
}

/**
 * 精确 Base URL 前缀白名单实现：
 * - 白名单条目在构造时即校验（HTTPS、无 userinfo/query/fragment；
 *   本机条目仅在 allowLocalhost 开启时可用），配置错误立即失败；
 * - 目标 URL 必须 HTTPS（本机端点在开关开启时可用 http），
 *   且 origin 相同并落在某条目的路径边界内。
 */
export class AllowlistModelEndpointPolicy implements ModelEndpointPolicy {
  private readonly prefixes: NormalizedPrefix[];
  private readonly allowLocalhost: boolean;

  constructor(options: AllowlistModelEndpointPolicyOptions) {
    this.allowLocalhost = options.allowLocalhost ?? false;
    this.prefixes = options.allowlist.map((entry) => {
      const url = parseStrict(entry, `白名单条目`, 500);
      const isLocal = isLocalHostname(url.hostname);
      if (isLocal) {
        if (!this.allowLocalhost) {
          throw new GatewayError(
            500,
            "configuration_error",
            `白名单条目为本机端点但未开启 GATEWAY_ALLOW_LOCALHOST_MODEL：${entry}`,
          );
        }
        if (url.protocol !== "http:" && url.protocol !== "https:") {
          throw new GatewayError(500, "configuration_error", `白名单条目协议必须是 http/https：${entry}`);
        }
      } else if (url.protocol !== "https:") {
        throw new GatewayError(500, "configuration_error", `白名单条目必须使用 HTTPS：${entry}`);
      }
      return { origin: url.origin, path: normalizePath(url.pathname) };
    });
  }

  assertAllowed(baseUrl: string): void {
    const url = parseStrict(baseUrl, "模型 Base URL ", 400);

    const isLocal = isLocalHostname(url.hostname);
    if (isLocal) {
      if (!this.allowLocalhost) {
        throw new GatewayError(
          403,
          "model_base_url_forbidden",
          "本机模型端点未开启（GATEWAY_ALLOW_LOCALHOST_MODEL）",
        );
      }
      if (url.protocol !== "http:" && url.protocol !== "https:") {
        throw new GatewayError(400, "invalid_model_base_url", "模型 Base URL 协议必须是 http/https");
      }
      // 本机端点豁免白名单（仅限开发）。
      return;
    }

    if (url.protocol !== "https:") {
      throw new GatewayError(400, "invalid_model_base_url", "非本机模型端点必须使用 HTTPS");
    }

    const candidatePath = normalizePath(url.pathname);
    const allowed = this.prefixes.some((prefix) => {
      if (prefix.origin !== url.origin) {
        return false;
      }
      if (prefix.path === "") {
        return true; // origin-only 条目：放行该 origin 全部路径。
      }
      return candidatePath === prefix.path || candidatePath.startsWith(`${prefix.path}/`);
    });
    if (!allowed) {
      throw new GatewayError(403, "model_base_url_forbidden", "模型 Base URL 不在白名单内");
    }
  }
}
