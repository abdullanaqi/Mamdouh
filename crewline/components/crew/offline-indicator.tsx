'use client';
import { CloudOff, RefreshCw } from 'lucide-react';
import { useOffline } from './offline-provider';

export function OfflineIndicator() {
  const { online, pending } = useOffline();
  if (online && pending === 0) return null;
  return (
    <span className="flex items-center gap-1 rounded-full bg-[var(--color-warning)] px-2 py-0.5 text-xs font-medium text-white">
      {online ? <RefreshCw className="size-3" /> : <CloudOff className="size-3" />}
      {online ? `Syncing ${pending}` : `Offline · ${pending} queued`}
    </span>
  );
}
