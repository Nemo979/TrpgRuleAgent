export interface RuleSearchRequest {
  query: string;
  rulesetId: string;
  limit?: number;
  sourceIds?: string[];
}

export interface RuleSearchHit {
  id: string;
  rulesetId: string;
  sourceId: string;
  sourceTitle: string;
  title: string;
  fullPath: string;
  excerpt: string;
  version: string;
  score: number;
  /** The child chunk that produced the excerpt, when the backend exposes it. */
  chunkId?: string | null;
  chunkIndex?: number | null;
  matchScore?: number;
  parentScore?: number;
  metadata: Record<string, unknown>;
}

export interface RuleDocument {
  id: string;
  rulesetId: string;
  sourceId: string;
  sourceTitle: string;
  title: string;
  fullPath: string;
  content: string;
  version: string;
  priority: number;
  metadata: Record<string, unknown>;
}

export interface ReadRulesRequest {
  rulesetId: string;
  ids: string[];
}

export interface RuleSource {
  id: string;
  title: string;
  rulesetId: string;
  version: string;
  priority: number;
  documentCount: number;
}

export interface HealthResponse {
  status: "ok";
  documentCount: number;
  rulesets: string[];
}
