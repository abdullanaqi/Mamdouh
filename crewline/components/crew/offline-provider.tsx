'use client';
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { MemoryStorage, OfflineQueue, type FetchLike, type QueueStorage } from '@/lib/offline/queue';
import { IndexedDBStorage, idbAvailable } from '@/lib/offline/idb';

type CrewMutateResult = { queued: boolean; ok: boolean };

type OfflineCtx = {
  online: boolean;
  pending: number;
  /**
   * Send a crew mutation. If online, POSTs immediately; if offline or the POST
   * fails, the action is queued and replayed when back online.
   */
  mutate: (endpoint: string, payload: Record<string, unknown>) => Promise<CrewMutateResult>;
  flush: () => Promise<void>;
};

const Ctx = createContext<OfflineCtx | null>(null);

function makeId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID();
  return `a-${Date.now()}-${Math.floor(Math.random() * 1e9)}`;
}

export function OfflineProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [online, setOnline] = useState(true);
  const [pending, setPending] = useState(0);
  const queueRef = useRef<OfflineQueue | null>(null);

  if (!queueRef.current) {
    const storage: QueueStorage = idbAvailable() ? new IndexedDBStorage() : new MemoryStorage();
    const fetchImpl: FetchLike = (input, init) =>
      fetch(input, init).then((r) => ({ ok: r.ok, status: r.status }));
    queueRef.current = new OfflineQueue(storage, fetchImpl);
  }

  const refreshPending = useCallback(async () => {
    const q = queueRef.current!;
    setPending(await q.pendingCount());
  }, []);

  const flush = useCallback(async () => {
    const q = queueRef.current!;
    const res = await q.flush();
    await refreshPending();
    if (res.synced > 0) router.refresh();
  }, [refreshPending, router]);

  const mutate = useCallback(
    async (endpoint: string, payload: Record<string, unknown>): Promise<CrewMutateResult> => {
      const q = queueRef.current!;
      const withTime = { clientTime: Date.now(), ...payload };
      if (typeof navigator !== 'undefined' && navigator.onLine) {
        try {
          const res = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(withTime),
          });
          if (res.ok) {
            router.refresh();
            return { queued: false, ok: true };
          }
          // 4xx are real rejections — surface, don't queue.
          if (res.status >= 400 && res.status < 500) return { queued: false, ok: false };
        } catch {
          // network blip — fall through to queue
        }
      }
      await q.enqueue(endpoint, withTime, makeId);
      await refreshPending();
      return { queued: true, ok: true };
    },
    [refreshPending, router],
  );

  useEffect(() => {
    setOnline(navigator.onLine);
    void refreshPending();
    void flush();
    const goOnline = () => {
      setOnline(true);
      void flush();
    };
    const goOffline = () => setOnline(false);
    window.addEventListener('online', goOnline);
    window.addEventListener('offline', goOffline);
    return () => {
      window.removeEventListener('online', goOnline);
      window.removeEventListener('offline', goOffline);
    };
  }, [flush, refreshPending]);

  return <Ctx.Provider value={{ online, pending, mutate, flush }}>{children}</Ctx.Provider>;
}

export function useOffline(): OfflineCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useOffline must be used within OfflineProvider');
  return ctx;
}
