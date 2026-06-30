/**
 * Geocoding (spec §7.5). Driver selected by GEOCODE_DRIVER:
 *  - "manual" (default): no external call; the owner supplies lat/lng in the
 *    site form. Returns null so the caller keeps whatever was entered.
 *  - "google": geocode the address via Google Maps Platform (needs key).
 *
 * Geofence distance math itself never needs a provider — see lib/domain/geofence.
 */
export type GeoPoint = { lat: number; lng: number };

export async function geocodeAddress(address: string): Promise<GeoPoint | null> {
  const driver = process.env.GEOCODE_DRIVER ?? 'manual';
  if (driver === 'manual') return null;

  if (driver === 'google') {
    const key = process.env.GOOGLE_MAPS_API_KEY;
    if (!key) {
      // Misconfiguration: fail soft (no coords) rather than crash site creation.
      console.warn('GEOCODE_DRIVER=google but GOOGLE_MAPS_API_KEY is unset; skipping geocode.');
      return null;
    }
    const url = new URL('https://maps.googleapis.com/maps/api/geocode/json');
    url.searchParams.set('address', address);
    url.searchParams.set('key', key);
    const res = await fetch(url, { cache: 'no-store' });
    if (!res.ok) {
      console.warn(`geocode failed: ${res.status}`);
      return null;
    }
    const json = (await res.json()) as {
      status: string;
      results?: Array<{ geometry?: { location?: { lat: number; lng: number } } }>;
    };
    const loc = json.results?.[0]?.geometry?.location;
    if (json.status !== 'OK' || !loc) return null;
    return { lat: loc.lat, lng: loc.lng };
  }

  return null;
}
