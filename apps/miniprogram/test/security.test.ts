import { describe, expect, it } from "vitest";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const SRC_DIR = fileURLToPath(new URL("../src", import.meta.url));

/** 读取 src 下所有 .ts 文件内容。 */
function readSrcFiles(): Array<{ name: string; content: string }> {
  return readdirSync(SRC_DIR)
    .filter((name) => name.endsWith(".ts"))
    .map((name) => ({ name, content: readFileSync(join(SRC_DIR, name), "utf8") }));
}

/**
 * 去除注释，避免文档性注释（如“绝不写入 wx Storage”）触发误报。
 * 仅用于静态安全断言：我们只关心“代码是否真的使用了这些危险 API”。
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

// 禁止出现的持久化 / 危险存储 / 日志泄漏 API。
const FORBIDDEN_TOKENS = [
  "setStorage",
  "getStorage",
  "removeStorage",
  "clearStorage",
  "localStorage",
  "sessionStorage",
  "indexedDB",
  "document.cookie",
  "innerHTML",
];

describe("apps/miniprogram 源码静态安全断言", () => {
  const files = readSrcFiles();

  it("src 下不应出现持久化存储 API（API Key/token 绝不落盘）", () => {
    for (const { name, content } of files) {
      const code = stripComments(content);
      for (const token of FORBIDDEN_TOKENS) {
        expect(code, `${name} 不应包含 ${token}`).not.toContain(token);
      }
    }
  });

  it("src 下不应引用 wx 全局对象（微信能力只能由宿主注入）", () => {
    for (const { name, content } of files) {
      const code = stripComments(content);
      expect(code, `${name} 不应直接调用 wx.*`).not.toMatch(/\bwx\s*[.[]/);
      expect(code, `${name} 不应引用 globalThis.wx`).not.toContain("globalThis.wx");
    }
  });

  it("src 下不应打印日志（console 可能把凭据带进 vConsole/真机日志）", () => {
    for (const { name, content } of files) {
      const code = stripComments(content);
      expect(code, `${name} 不应包含 console.`).not.toContain("console.");
    }
  });

  it("session-facade.ts 只允许把 apiKey 存入 #private 私有字段", () => {
    const content = readFileSync(join(SRC_DIR, "session-facade.ts"), "utf8");
    expect(content).not.toContain("this.apiKey");
    expect(content).not.toMatch(/[^#.\w]apiKey\s*:\s*string\s*=/);
    // 私有字段存在（内存态语义），且没有任何公开 getter 返回它。
    expect(content).toContain("#apiKey");
    expect(content).not.toMatch(/get\s+apiKey/);
  });

  it("wx-host.ts 不接触凭据字段（只透传 SDK 构造的 header）", () => {
    const content = stripComments(readFileSync(join(SRC_DIR, "wx-host.ts"), "utf8"));
    expect(content).not.toContain("apiKey");
    expect(content).not.toContain("Authorization");
    expect(content).not.toContain("X-Model-Api-Key");
  });

  it("wx-host.ts 不伪造状态码/内容类型（早到 chunk 必须缓存回放而非补 200 event-stream）", () => {
    const content = stripComments(readFileSync(join(SRC_DIR, "wx-host.ts"), "utf8"));
    // 禁止再出现「硬编码 text/event-stream 响应头」这类伪造逻辑：
    // content-type 只能来自宿主真实响应头。
    expect(content).not.toContain('"content-type": "text/event-stream"');
    expect(content).not.toContain("text/event-stream");
    // 必须存在缓存回放机制。
    expect(content).toContain("pendingChunks");
  });
});
