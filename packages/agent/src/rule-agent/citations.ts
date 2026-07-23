import type { RuleDocument } from "@trpg-rule-agent/rules-types";

export interface RegisteredCitation {
  label: string;
  document: RuleDocument;
}

export class CitationRegistry {
  private readonly byDocumentId = new Map<string, RegisteredCitation>();

  register(document: RuleDocument): RegisteredCitation {
    const existing = this.byDocumentId.get(document.id);
    if (existing) {
      return existing;
    }

    const citation = {
      label: `S${this.byDocumentId.size + 1}`,
      document,
    } satisfies RegisteredCitation;
    this.byDocumentId.set(document.id, citation);
    return citation;
  }

  list(): RegisteredCitation[] {
    return [...this.byDocumentId.values()];
  }

  formatSources(): string {
    const citations = this.list();
    if (citations.length === 0) {
      return "";
    }
    return citations.map(({ label, document }) => `[${label}] ${document.fullPath}`).join("\n");
  }

  clear(): void {
    this.byDocumentId.clear();
  }
}
