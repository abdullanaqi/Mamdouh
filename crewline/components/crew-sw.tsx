'use client';
import { useEffect } from 'react';

/** Registers the crew PWA service worker (offline-tolerant "today" view). */
export function CrewServiceWorker() {
  useEffect(() => {
    if (typeof navigator === 'undefined' || !('serviceWorker' in navigator)) return;
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch((err) => {
      console.warn('SW registration failed:', err);
    });
  }, []);
  return null;
}
