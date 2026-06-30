import { createReadStream, existsSync } from 'node:fs';
import { Readable } from 'node:stream';
import type { NextRequest } from 'next/server';
import { localStoragePath } from '@/lib/integrations/storage';
import { getAuth } from '@/lib/auth/context';

/**
 * Serves proof photos stored by the local storage driver. Access is scoped:
 * the object key is prefixed with the owning org id, and we only serve it to an
 * authenticated member of that org.
 */
export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ key: string[] }> },
) {
  const auth = await getAuth();
  if (!auth) return new Response('Unauthorized', { status: 401 });

  const { key } = await params;
  const orgPrefix = key[0];
  if (orgPrefix !== auth.orgId) return new Response('Forbidden', { status: 403 });

  const full = localStoragePath(key.join('/'));
  if (!existsSync(full)) return new Response('Not found', { status: 404 });

  const ext = full.split('.').pop()?.toLowerCase();
  const contentType =
    ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : 'image/jpeg';

  const stream = Readable.toWeb(createReadStream(full)) as ReadableStream;
  return new Response(stream, {
    headers: { 'Content-Type': contentType, 'Cache-Control': 'private, max-age=3600' },
  });
}
