import { createInterface } from "node:readline/promises";
import { readFile } from "node:fs/promises";
import { homedir } from "node:os";
import { argv, stdin, stdout } from "node:process";
import { createConfiguredModel, createRuleAgent } from "@trpg-rule-agent/agent";
import { RulesClient } from "@trpg-rule-agent/rules-client";

async function resolveApiKey(): Promise<string | undefined> {
  if (process.env.LLM_API_KEY) {
    return process.env.LLM_API_KEY;
  }

  const provider = process.env.LLM_PI_AUTH_PROVIDER;
  if (!provider) {
    return undefined;
  }

  const authPath = `${homedir()}/.pi/agent/auth.json`;
  const auth = JSON.parse(await readFile(authPath, "utf8")) as Record<
    string,
    { key?: unknown }
  >;
  const key = auth[provider]?.key;
  if (typeof key !== "string" || !key) {
    throw new Error(`Pi 认证文件中没有可用的 ${provider} API Key`);
  }
  return key;
}

const apiKey = await resolveApiKey();
if (!apiKey) {
  throw new Error("必须配置 LLM_API_KEY 或 LLM_PI_AUTH_PROVIDER");
}

const rulesetId = process.env.RULESET_ID ?? "pathfinder-1e";
const client = new RulesClient(process.env.RETRIEVAL_BASE_URL ?? "http://127.0.0.1:8765");
const health = await client.health();
if (!health.rulesets.includes(rulesetId)) {
  throw new Error(`检索服务没有加载规则集：${rulesetId}`);
}

const runtime = createRuleAgent({
  model: createConfiguredModel(process.env),
  apiKey,
  client,
  rulesetId,
});

runtime.agent.subscribe((event) => {
  if (event.type === "tool_execution_start") {
    stdout.write(`\n[tool] ${event.toolName}\n`);
  }
  if (event.type === "message_update" && event.assistantMessageEvent.type === "text_delta") {
    stdout.write(event.assistantMessageEvent.delta);
  }
});

async function answer(input: string): Promise<void> {
  runtime.resetTurnState();
  stdout.write("助手：");
  await runtime.agent.prompt(input);
  const sources = runtime.citations.formatSources();
  if (sources) {
    stdout.write(`\n\n来源：\n${sources}\n`);
  }
}

const oneShotQuestion = argv.slice(2).join(" ").trim();
if (oneShotQuestion) {
  await answer(oneShotQuestion);
  process.exit(0);
}

stdout.write(`TrpgRuleAgent 已启动，规则集：${rulesetId}\n输入 /exit 退出。\n`);
const readline = createInterface({ input: stdin, output: stdout });

while (true) {
  const input = (await readline.question("\n你：")).trim();
  if (input === "/exit") {
    break;
  }
  if (!input) {
    continue;
  }

  await answer(input);
}

readline.close();
