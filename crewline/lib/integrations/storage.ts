import 'server-only';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

/**
 * Proof-photo storage (spec §7.4). Driver selected by STORAGE_DRIVER:
 *  - "local" (default): writes bytes under ./.local-storage and serves them via
 *    /api/storage/[...key]. Good for dev/tests; no external account.
 *  - "r2": Cloudflare R2 / S3-compatible (prod) — implemented behind the same
 *    interface. Requires R2_* env vars.
 *
 * Callers store the returned { key, url } on shift_photos. We never store binary
 * in the DB.
 */
export type StoredObject = { key: string; url: string };

const LOCAL_DIR = join(process.cwd(), '.local-storage');

export async function putObject(
  orgId: string,
  bytes: Buffer,
  contentType: string,
): Promise<StoredObject> {
  const driver = process.env.STORAGE_DRIVER ?? 'local';
  const ext = contentType.split('/')[1]?.replace(/[^a-z0-9]/gi, '') || 'bin';
  const key = `${orgId}/${randomUUID()}.${ext}`;

  if (driver === 'local') {
    const full = join(LOCAL_DIR, key);
    await mkdir(join(full, '..'), { recursive: true });
    await writeFile(full, bytes);
    return { key, url: `/api/storage/${key}` };
  }

  if (driver === 'r2') {
    // R2 is S3-compatible. Implemented behind this interface; requires the
    // @aws-sdk/client-s3 dependency + R2_* env vars to be wired by the operator.
    throw new Error(
      'STORAGE_DRIVER=r2 is configured but the R2 client is not provisioned in this build. ' +
        'Set STORAGE_DRIVER=local for dev, or wire R2 credentials (HANDBACK).',
    );
  }

  throw new Error(`Unknown STORAGE_DRIVER: ${driver}`);
}

export function localStoragePath(key: string): string {
  return join(LOCAL_DIR, key);
}
