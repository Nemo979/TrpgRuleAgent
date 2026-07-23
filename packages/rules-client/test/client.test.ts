import { afterEach, describe, expect, it, vi } from "vitest";
import { RulesClient, RulesClientError } from "../src/index.ts";

describe("RulesClient", () => {
  it("supports injected fetch and cloud request headers", async () => {
    const calls: RequestInit[] = [];
    const client = new RulesClient("https://rules.example", {
      headers: { authorization: "Bearer server-token" },
      fetch: async (_input, init) => {
        calls.push(init ?? {});
        return new Response(JSON.stringify({ data: { status: "ok", documentCount: 0, rulesets: [] } }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      },
    });

    await expect(client.health()).resolves.toEqual({ status: "ok", documentCount: 0, rulesets: [] });
    expect(calls[0]?.headers).toMatchObject({ authorization: "Bearer server-token" });
    expect(calls[0]?.signal).toBeInstanceOf(AbortSignal);
  });

  it("turns an unresponsive cloud service into a 504 error", async () => {
    const client = new RulesClient("https://rules.example", {
      timeoutMs: 5,
      fetch: async (_input, init) =>
        new Promise((_resolve, reject) => {
          init?.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
        }),
    });

    await expect(client.health()).rejects.toMatchObject({ status: 504, message: "检索服务请求超时" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("解包检索服务的 data 响应", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      data: {
        status: "ok",
        documentCount: 3,
        rulesets: ["pathfinder-1e"],
      },
    }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    const client = new RulesClient("http://127.0.0.1:8765/");
    await expect(client.health()).resolves.toEqual({
      status: "ok",
      documentCount: 3,
      rulesets: ["pathfinder-1e"],
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8765/health",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("将服务端错误转换为带状态码的异常", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: "bad request" }),
      { status: 400 },
    )));

    const client = new RulesClient("http://127.0.0.1:8765");
    const error = await client.search({
      query: "",
      rulesetId: "pathfinder-1e",
    }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(RulesClientError);
    expect((error as RulesClientError).status).toBe(400);
  });
});
