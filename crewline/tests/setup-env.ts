// Loads .env.test into every vitest worker before any test module imports
// lib/db (which reads DATABASE_URL at import time).
import { config } from 'dotenv';
config({ path: '.env.test' });

if (!process.env.DATABASE_URL) {
  process.env.DATABASE_URL = 'postgresql://postgres@127.0.0.1:5433/crewline_test';
}
if (!process.env.AUTH_SECRET) {
  process.env.AUTH_SECRET = 'test_secret_0123456789abcdef0123456789abcdef';
}
process.env.EMAIL_DRIVER ??= 'log';
process.env.STORAGE_DRIVER ??= 'local';
process.env.GEOCODE_DRIVER ??= 'manual';
