import { describe, expect, it } from "vitest";
import { loadGatewayConfig } from "../src/config.ts";

describe("loadGatewayConfig retrieval credentials", () => {
  it("loads server-only retrieval API key without changing session config", () => {
    const config = loadGatewayConfig({ RETRIEVAL_API_KEY: "cloud-secret" });
    expect(config.retrievalApiKey).toBe("cloud-secret");
    expect(config.retrievalBaseUrl).toBe("http://127.0.0.1:8765");
  });

  it("does not create a retrieval credential when unset", () => {
    const config = loadGatewayConfig({});
    expect(config).not.toHaveProperty("retrievalApiKey");
  });

  it("rejects retrieval URLs carrying credentials or query parameters", () => {
    expect(() => loadGatewayConfig({ RETRIEVAL_BASE_URL: "https://user:pass@rules.example/api" })).toThrow(
      "RETRIEVAL_BASE_URL",
    );
    expect(() => loadGatewayConfig({ RETRIEVAL_BASE_URL: "https://rules.example/api?token=secret" })).toThrow(
      "RETRIEVAL_BASE_URL",
    );
  });
});
