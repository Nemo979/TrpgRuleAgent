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
      throw new Error(`本轮最多允许搜索 ${this.limits.maxSearchCalls} 次`);
    }
    this.searchCalls += 1;
  }

  consumeRead(requestedDocuments: number): void {
    this.consumeToolCall();
    if (this.documentsRead + requestedDocuments > this.limits.maxDocumentsRead) {
      throw new Error(`本轮最多允许读取 ${this.limits.maxDocumentsRead} 篇完整规则文档`);
    }
    this.documentsRead += requestedDocuments;
  }

  private consumeToolCall(): void {
    if (this.toolCalls >= this.limits.maxToolCalls) {
      throw new Error(`本轮最多允许执行 ${this.limits.maxToolCalls} 次规则工具`);
    }
    this.toolCalls += 1;
  }
}
