import { AgentError } from "../core/errors.ts";

export interface ToolBudgetLimits {
  maxToolCalls: number;
  maxSearchCalls: number;
  maxDocumentsRead: number;
}

export class ToolBudget {
  private toolCalls = 0;
  private searchCalls = 0;
  private documentsRead = 0;
  readonly limits: ToolBudgetLimits;

  constructor(limits: ToolBudgetLimits) {
    this.limits = limits;
  }

  consumeSearch(): void {
    this.consumeToolCall();
    if (this.searchCalls >= this.limits.maxSearchCalls) {
      throw new AgentError("limit_exceeded", `本轮最多允许搜索 ${this.limits.maxSearchCalls} 次`);
    }
    this.searchCalls += 1;
  }

  consumeRead(requestedDocuments: number): void {
    this.consumeToolCall();
    if (this.documentsRead + requestedDocuments > this.limits.maxDocumentsRead) {
      throw new AgentError(
        "limit_exceeded",
        `本轮最多允许读取 ${this.limits.maxDocumentsRead} 篇完整规则文档`,
      );
    }
    this.documentsRead += requestedDocuments;
  }

  /** 每个用户问题开始前重置当轮预算。 */
  reset(): void {
    this.toolCalls = 0;
    this.searchCalls = 0;
    this.documentsRead = 0;
  }

  private consumeToolCall(): void {
    if (this.toolCalls >= this.limits.maxToolCalls) {
      throw new AgentError(
        "limit_exceeded",
        `本轮最多允许执行 ${this.limits.maxToolCalls} 次规则工具`,
      );
    }
    this.toolCalls += 1;
  }
}
