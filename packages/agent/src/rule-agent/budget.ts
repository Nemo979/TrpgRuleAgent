import { AgentError } from "../core/errors.ts";

export interface ToolBudgetLimits {
  maxToolCalls: number;
  maxSearchCalls: number;
  maxDocumentsRead: number;
  maxEvidenceCharacters?: number;
}

export class ToolBudget {
  private toolCalls = 0;
  private searchCalls = 0;
  private documentsRead = 0;
  private evidenceCharacters = 0;
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

  /** Consume one read tool call before the provider request is made. */
  consumeReadToolCall(): void {
    this.consumeToolCall();
  }

  /** Admit returned documents independently so one oversized document is degradable. */
  admitDocuments<T extends { content: string }>(documents: T[]): T[] {
    const accepted: T[] = [];
    const maxCharacters = this.limits.maxEvidenceCharacters ?? 80_000;
    for (const document of documents) {
      const characters = document.content.length;
      const acceptedCharacters = accepted.reduce((sum, item) => sum + item.content.length, 0);
      if (this.documentsRead + accepted.length >= this.limits.maxDocumentsRead) {
        continue;
      }
      if (this.evidenceCharacters + acceptedCharacters + characters > maxCharacters) {
        continue;
      }
      accepted.push(document);
    }
    this.documentsRead += accepted.length;
    this.evidenceCharacters += accepted.reduce((sum, item) => sum + item.content.length, 0);
    return accepted;
  }

  /** 每个用户问题开始前重置当轮预算。 */
  reset(): void {
    this.toolCalls = 0;
    this.searchCalls = 0;
    this.documentsRead = 0;
    this.evidenceCharacters = 0;
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
