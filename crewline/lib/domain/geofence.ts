/**
 * Geofence math (spec §7.5): Haversine distance, no external service needed.
 */
const EARTH_RADIUS_M = 6_371_000;

function toRad(deg: number): number {
  return (deg * Math.PI) / 180;
}

/** Great-circle distance in meters between two lat/lng points. */
export function haversineMeters(
  aLat: number,
  aLng: number,
  bLat: number,
  bLng: number,
): number {
  const dLat = toRad(bLat - aLat);
  const dLng = toRad(bLng - aLng);
  const lat1 = toRad(aLat);
  const lat2 = toRad(bLat);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.sin(dLng / 2) ** 2 * Math.cos(lat1) * Math.cos(lat2);
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(h)));
}

export type GeofenceResult = { withinGeofence: boolean; distanceM: number };

/**
 * Decide whether a clock-in location is within a site's geofence.
 * If the site has no coordinates, we cannot verify — treat as not-within but
 * caller may choose to allow (returned distance is Infinity).
 */
export function checkGeofence(
  site: { lat: number | null; lng: number | null; geofenceRadiusM: number },
  clockInLat: number,
  clockInLng: number,
): GeofenceResult {
  if (site.lat == null || site.lng == null) {
    return { withinGeofence: false, distanceM: Infinity };
  }
  const distanceM = haversineMeters(site.lat, site.lng, clockInLat, clockInLng);
  return { withinGeofence: distanceM <= site.geofenceRadiusM, distanceM };
}
