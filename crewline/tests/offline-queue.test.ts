import { describe, expect, it, vi } from 'vitest';
import { MemoryStorage, OfflineQueue, type FetchLike } from '@/lib/offline/queue';

let counter = 0;
const idFactory = () => `id-${++counter}`;

describe('OfflineQueue', () => {
  it('enqueues actions with a client timestamp', async () => {
    const storage = new MemoryStorage();
    const queue = new OfflineQueue(storage, (async () => ({ ok: true, status: 200 })) as FetchLike);
    await queue.enqueue('/api/crew/clock-in', { shiftId: 's1', lat: 1, lng: 2 }, idFactory);
    const all = await storage.getAll();
    expect(all.length).toBe(1);
    expect(all[0].endpoint).toBe('/api/crew/clock-in');
    expect(all[0].payload.clientTime).toBeTypeOf('number');
  });

  it('flush replays all queued actions and clears them on success', async () => {
    const storage = new MemoryStorage();
    const fetchImpl = vi.fn(async () => ({ ok: true, status: 200 })) as unknown as FetchLike;
    const queue = new OfflineQueue(storage, fetchImpl);
    await queue.enqueue('/api/crew/clock-in', { shiftId: 's1' }, idFactory);
    await queue.enqueue('/api/crew/checklist', { shiftId: 's1', resultId: 'r1', completed: true }, idFactory);

    const result = await queue.flush();
    expect(result.synced).toBe(2);
    expect(result.remaining).toBe(0);
    expect(await queue.pendingCount()).toBe(0);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it('keeps actions queued on network failure, then syncs when back online', async () => {
    const storage = new MemoryStorage();
    let online = false;
    const fetchImpl = (async () => {
      if (!online) throw new Error('offline');
      return { ok: true, status: 200 };
    }) as FetchLike;
    const queue = new OfflineQueue(storage, fetchImpl);

    await queue.enqueue('/api/crew/clock-in', { shiftId: 's1' }, idFactory);
    const offlineFlush = await queue.flush();
    expect(offlineFlush.synced).toBe(0);
    expect(offlineFlush.remaining).toBe(1);

    online = true;
    const onlineFlush = await queue.flush();
    expect(onlineFlush.synced).toBe(1);
    expect(onlineFlush.remaining).toBe(0);
  });

  it('drops non-retryable 4xx so a poison message cannot block the queue', async () => {
    const storage = new MemoryStorage();
    const fetchImpl = (async () => ({ ok: false, status: 422 })) as FetchLike;
    const queue = new OfflineQueue(storage, fetchImpl);
    await queue.enqueue('/api/crew/checklist', { bad: true }, idFactory);
    const result = await queue.flush();
    expect(result.failed).toBe(1);
    expect(result.remaining).toBe(0);
  });
});
