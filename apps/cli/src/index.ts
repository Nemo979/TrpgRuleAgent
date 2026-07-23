import { createInterface } from "node:readline/promises";
import { argv, stdin, stdout, stderr, exit } from "node:process";
import {
  AgentError,
  createRuleAgent,
  loadRuleAgentConfig,
  loadRuleAgentCredentials,
  type RuleAgentConfig,
  type RuleAgentCredentials,
} from "@trpg-rule-agent/agent";
import { RulesClient } from "@trpg-rule-agent/rules-client";

function fail(message: string): never {
  stderr.write(`${message}\n`);
  exit(1);
}

let config: RuleAgentConfig;
let credentials: RuleAgentCredentials;
try {
  config = loadRuleAgentConfig(process.env);
  credentials = loadRuleAgentCredentials(process.env);
} catch (error) {
  fail(error instanceof AgentError ? `配置错误：${error.message}` : String(error));
}

const client = new RulesClient(config.retrievalBaseUrl);
const health = await client.health();
if (!health.rulesets.includes(config.rulesetId)) {
  fail(`检索服务没有加载规则集：${config.rulesetId}`);
}

const agent = createRuleAgent({ config, client });

async function answer(input: string): Promise<boolean> {
  agent.resetTurnState();
  stdout.write("助手：");
  let succeeded = true;
  for await (const event of agent.run(input, { apiKey: credentials.apiKey })) {
    switch (event.type) {
      case "text_delta":
        stdout.write(event.delta);
        break;
      case "tool_start":
        stdout.write(`\n[tool] ${event.toolName}\n`);
        break;
      case "tool_error":
        stderr.write(`\n[tool_error] ${event.toolName}: ${event.error.message}\n`);
        break;
      case "error":
        succeeded = false;
        stderr.write(`\n[错误] (${event.error.category}) ${event.error.message}\n`);
        break;
      default:
        break;
    }
  }

  const sources = agent.citations.formatSources();
  if (sources) {
    stdout.write(`\n\n来源：\n${sources}\n`);
  }
  return succeeded;
}

const oneShotQuestion = argv.slice(2).join(" ").trim();
if (oneShotQuestion) {
  const succeeded = await answer(oneShotQuestion);
  exit(succeeded ? 0 : 1);
}

stdout.write(`TrpgRuleAgent 已启动，规则集：${config.rulesetId}\n输入 /exit 退出。\n`);
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
