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
 * 去除注释，避免文档性注释（如“不触碰 localStorage”）触发误报。
 * 仅用于静态安全断言：我们只关心“代码是否真的使用了这些危险 API”。
 */
function stripComments(src: string): string {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
}

// 禁止出现的持久化 / 危险存储与 XSS 注入 API。
const FORBIDDEN_TOKENS = [
  "localStorage",
  "sessionStorage",
  "indexedDB",
  "document.cookie",
  "innerHTML",
];

describe("源码静态安全断言", () => {
  const files = readSrcFiles();

  it("src 下不应出现持久化/危险存储与 XSS 注入 API", () => {
    for (const { name, content } of files) {
      const code = stripComments(content);
      for (const token of FORBIDDEN_TOKENS) {
        expect(code, `${name} 不应包含 ${token}`).not.toContain(token);
      }
    }
  });

  it("browser-transport.ts 的 fetch init 使用 redirect: \"error\"", () => {
    const content = readFileSync(join(SRC_DIR, "browser-transport.ts"), "utf8");
    expect(content).toContain('redirect: "error"');
  });

  it("client.ts 不把 apiKey 写入任何实例字段", () => {
    const content = readFileSync(join(SRC_DIR, "client.ts"), "utf8");
    expect(content).not.toContain("this.#apiKey");
    expect(content).not.toContain("this.apiKey =");
    expect(content).not.toContain("this.apiKey=");
  });
});
