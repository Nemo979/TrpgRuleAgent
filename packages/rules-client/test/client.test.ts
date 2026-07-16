import { afterEach, describe, expect, it, vi } from "vitest";
import { RulesClient, RulesClientError } from "../src/index.ts";

describe("RulesClient", () => {
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
