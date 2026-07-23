import { createHash, randomBytes } from "node:crypto";
import type { AgentMessage } from "@trpg-rule-agent/agent";
import type { SessionConnectionConfig } from "./session-config.ts";

/**
 * 会话快照。刻意不存在任何凭据字段：
 * 模型 API Key 永远不进入 SessionStore；config 只含非敏感连接配置。
 */
export interface SessionSnapshot {
  /** 会话绑定的非敏感模型连接配置（BYOK，创建时校验后固定）。 */
  config: SessionConnectionConfig;
  /** 会话累计的 Agent 消息历史（system/user/assistant/tool）。 */
  messages: AgentMessage[];
  /** 会话创建时间（epoch 毫秒）。 */
  createdAt: number;
  /** 最近一次成功访问时间（epoch 毫秒）；expiresAt = lastAccessAt + ttl。 */
  lastAccessAt: number;
  /** 会话过期时间（epoch 毫秒）。 */
  expiresAt: number;
  /** 历史写入版本号：saveMessages 每次成功 +1（为未来乐观并发保留）。 */
  version: number;
}

export interface SessionCreation {
  /** 原始 token 只在此处返回一次；服务端只保留 SHA-256 摘要。 */
  sessionToken: string;
  expiresAt: number;
}

/**
 * 会话存储抽象。全部方法返回 Promise，未来可替换为 Redis/数据库实现
 * 以支持多实例与多租户；接口层面已保证：不接收、不返回任何模型凭据。
 */
export interface SessionStore {
  create(config: SessionConnectionConfig): Promise<SessionCreation>;
  /** 按 token 访问会话；命中则续期（空闲 TTL），过期/不存在返回 undefined。 */
  touch(sessionToken: string): Promise<SessionSnapshot | undefined>;
  /** 标记会话进入 turn；会话不存在返回 "not_found"，已有进行中 turn 返回 "busy"。 */
  beginTurn(sessionToken: string): Promise<"ok" | "busy" | "not_found">;
  /** 结束 turn 并续期空闲 TTL（长 turn 不应导致会话过期）。 */
  endTurn(sessionToken: string): Promise<void>;
  /** 覆盖保存会话消息历史（仅在 turn 成功后调用），version +1。 */
  saveMessages(sessionToken: string, messages: AgentMessage[]): Promise<void>;
  delete(sessionToken: string): Promise<boolean>;
}

export interface InMemorySessionStoreOptions {
  /** 空闲 TTL（毫秒），默认 30 分钟。 */
  ttlMs?: number;
  /** 可注入时钟，测试可控地推进时间。 */
  now?: () => number;
}

interface StoredSession {
  config: SessionConnectionConfig;
  messages: AgentMessage[];
  createdAt: number;
  lastAccessAt: number;
  expiresAt: number;
  version: number;
  turnInFlight: boolean;
}

const TOKEN_BYTES = 32;
const DEFAULT_TTL_MS = 30 * 60 * 1000;

function digest(sessionToken: string): string {
  return createHash("sha256").update(sessionToken).digest("hex");
}

/**
 * 进程内会话存储：
 * - token 由 randomBytes(32) 生成，仅以 SHA-256 摘要为键存储；
 * - 空闲 TTL：每次成功访问续期；beginTurn/endTurn/saveMessages 同样续期，
 *   且 turn 进行中（turnInFlight）的会话不会因超过 TTL 被删除——
 *   长 turn 结束时 saveMessages/endTurn 仍然可达并再次续期；
 * - 记录结构中不存在凭据字段。
 * 内部操作同步完成，但公开接口返回 Promise 以与 SessionStore 契约一致。
 */
export class InMemorySessionStore implements SessionStore {
  private readonly sessions = new Map<string, StoredSession>();
  private readonly ttlMs: number;
  private readonly now: () => number;

  constructor(options: InMemorySessionStoreOptions = {}) {
    this.ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
    this.now = options.now ?? (() => Date.now());
  }

  create(config: SessionConnectionConfig): Promise<SessionCreation> {
    const sessionToken = randomBytes(TOKEN_BYTES).toString("base64url");
    const now = this.now();
    this.sessions.set(digest(sessionToken), {
      config: structuredClone(config),
      messages: [],
      createdAt: now,
      lastAccessAt: now,
      expiresAt: now + this.ttlMs,
      version: 0,
      turnInFlight: false,
    });
    return Promise.resolve({ sessionToken, expiresAt: now + this.ttlMs });
  }

  touch(sessionToken: string): Promise<SessionSnapshot | undefined> {
    const record = this.getLive(sessionToken);
    if (!record) {
      return Promise.resolve(undefined);
    }
    this.renew(record);
    return Promise.resolve(this.snapshot(record));
  }

  beginTurn(sessionToken: string): Promise<"ok" | "busy" | "not_found"> {
    const record = this.getLive(sessionToken);
    if (!record) {
      return Promise.resolve("not_found");
    }
    if (record.turnInFlight) {
      return Promise.resolve("busy");
    }
    record.turnInFlight = true;
    this.renew(record);
    return Promise.resolve("ok");
  }

  endTurn(sessionToken: string): Promise<void> {
    const record = this.getLive(sessionToken);
    if (record) {
      record.turnInFlight = false;
      this.renew(record);
    }
    return Promise.resolve();
  }

  saveMessages(sessionToken: string, messages: AgentMessage[]): Promise<void> {
    const record = this.getLive(sessionToken);
    if (record) {
      // 深拷贝：防止调用方后续修改 message 对象污染 Store 内部记录。
      record.messages = structuredClone(messages);
      record.version += 1;
      this.renew(record);
    }
    return Promise.resolve();
  }

  delete(sessionToken: string): Promise<boolean> {
    return Promise.resolve(this.sessions.delete(digest(sessionToken)));
  }

  /** 测试辅助：当前存活会话数（顺带清理过期且非进行中的项）。 */
  size(): number {
    for (const [key, record] of this.sessions) {
      if (record.expiresAt <= this.now() && !record.turnInFlight) {
        this.sessions.delete(key);
      }
    }
    return this.sessions.size;
  }

  /** 测试辅助：内部键列表（应全部为 64 位十六进制摘要，绝无原始 token）。 */
  keys(): string[] {
    return [...this.sessions.keys()];
  }

  /** 成功访问后刷新 lastAccessAt，并据此重算 expiresAt = lastAccessAt + ttl。 */
  private renew(record: StoredSession): void {
    record.lastAccessAt = this.now();
    record.expiresAt = record.lastAccessAt + this.ttlMs;
  }

  /** 生成对外快照：config 与 messages 均深拷贝，隔离调用方与内部记录。 */
  private snapshot(record: StoredSession): SessionSnapshot {
    return {
      config: structuredClone(record.config),
      messages: structuredClone(record.messages),
      createdAt: record.createdAt,
      lastAccessAt: record.lastAccessAt,
      expiresAt: record.expiresAt,
      version: record.version,
    };
  }

  private getLive(sessionToken: string): StoredSession | undefined {
    const key = digest(sessionToken);
    const record = this.sessions.get(key);
    if (!record) {
      return undefined;
    }
    // turn 进行中的会话不因空闲 TTL 过期，保证长 turn 收尾时可写回。
    if (record.expiresAt <= this.now() && !record.turnInFlight) {
      this.sessions.delete(key);
      return undefined;
    }
    return record;
  }
}
