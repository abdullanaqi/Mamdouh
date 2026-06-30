/**
 * Offline action queue for the crew PWA (spec §8.3, T1.3).
 *
 * Crew mutations (clock-in, checklist toggle, clock-out, issue) are POSTed to
 * idempotent JSON API routes. When offline (or a POST fails on bad signal) the
 * action is persisted locally and replayed when connectivity returns. The
 * stored payload carries a client timestamp so the recorded time reflects when
 * the crew actually acted, not when it synced.
 *
 * Storage is pluggable so the queue logic is unit-testable without a browser
 * (MemoryStorage in tests; IndexedDBStorage in the PWA).
 */
export type QueuedAction = {
  id: string;
  endpoint: string; // e.g. '/api/crew/clock-in'
  payload: Record<string, unknown>;
  createdAt: number;
};

export interface QueueStorage {
  getAll(): Promise<QueuedAction[]>;
  add(action: QueuedAction): Promise<void>;
  remove(id: string): Promise<void>;
}

export class MemoryStorage implements QueueStorage {
  private items: QueuedAction[] = [];
  async getAll() {
    return [...this.items];
  }
  async add(action: QueuedAction) {
    this.items.push(action);
  }
  async remove(id: string) {
    this.items = this.items.filter((i) => i.id !== id);
  }
}

export type FetchLike = (
  input: string,
  init: { method: string; headers: Record<string, string>; body: string },
) => Promise<{ ok: boolean; status: number }>;

export type FlushResult = { synced: number; remaining: number; failed: number };

export class OfflineQueue {
  constructor(
    private storage: QueueStorage,
    private fetchImpl: FetchLike,
  ) {}

  async enqueue(
    endpoint: string,
    payload: Record<string, unknown>,
    idFactory: () => string,
  ): Promise<QueuedAction> {
    const action: QueuedAction = {
      id: idFactory(),
      endpoint,
      payload: { ...payload, clientTime: payload.clientTime ?? Date.now() },
      createdAt: Date.now(),
    };
    await this.storage.add(action);
    return action;
  }

  async pendingCount(): Promise<number> {
    return (await this.storage.getAll()).length;
  }

  /**
   * Replay queued actions oldest-first. Stops removing on the first hard
   * failure of an item but continues attempting the rest; a 4xx (client error,
   * non-retryable) is dropped so a poison message can't block the queue forever.
   */
  async flush(): Promise<FlushResult> {
    const items = (await this.storage.getAll()).sort((a, b) => a.createdAt - b.createdAt);
    let synced = 0;
    let failed = 0;
    for (const item of items) {
      try {
        const res = await this.fetchImpl(item.endpoint, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(item.payload),
        });
        if (res.ok) {
          await this.storage.remove(item.id);
          synced += 1;
        } else if (res.status >= 400 && res.status < 500) {
          // Non-retryable client error — drop it so it can't wedge the queue.
          await this.storage.remove(item.id);
          failed += 1;
        } else {
          failed += 1; // server/5xx — keep for next flush
        }
      } catch {
        failed += 1; // network error — keep for next flush
      }
    }
    const remaining = (await this.storage.getAll()).length;
    return { synced, remaining, failed };
  }
}
