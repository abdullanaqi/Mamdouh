import { describe, expect, it } from 'vitest';
import { checkGeofence, haversineMeters } from '@/lib/domain/geofence';

describe('haversineMeters', () => {
  it('is ~0 for identical points', () => {
    expect(haversineMeters(41.8781, -87.6298, 41.8781, -87.6298)).toBeCloseTo(0, 5);
  });

  it('matches a known distance (Chicago ~ 1 city block)', () => {
    // ~111m apart in latitude (0.001 deg).
    const d = haversineMeters(41.8781, -87.6298, 41.8771, -87.6298);
    expect(d).toBeGreaterThan(100);
    expect(d).toBeLessThan(120);
  });
});

describe('checkGeofence', () => {
  const site = { lat: 41.8781, lng: -87.6298, geofenceRadiusM: 150 };

  it('within radius -> withinGeofence true', () => {
    const r = checkGeofence(site, 41.8782, -87.6299);
    expect(r.withinGeofence).toBe(true);
    expect(r.distanceM).toBeLessThan(150);
  });

  it('outside radius -> withinGeofence false', () => {
    const r = checkGeofence(site, 41.8800, -87.6400);
    expect(r.withinGeofence).toBe(false);
    expect(r.distanceM).toBeGreaterThan(150);
  });

  it('missing site coords -> not within, infinite distance', () => {
    const r = checkGeofence({ lat: null, lng: null, geofenceRadiusM: 150 }, 41.8, -87.6);
    expect(r.withinGeofence).toBe(false);
    expect(r.distanceM).toBe(Infinity);
  });
});
