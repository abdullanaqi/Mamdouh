'use server';
import { revalidatePath } from 'next/cache';
import { requireOwner } from '@/lib/auth/context';
import { createClient, updateClient } from '@/lib/domain/clients';
import { createSite, updateSite, replaceChecklist } from '@/lib/domain/sites';
import { DomainError } from '@/lib/domain/errors';
import { clientInput, siteInput, type ChecklistItemInput } from '@/lib/domain/schemas';

export type FormState = { error?: string; ok?: boolean; id?: string };

function num(v: FormDataEntryValue | null): number | undefined {
  if (v == null || v === '') return undefined;
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

export async function createClientAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  try {
    const parsed = clientInput.parse({
      name: formData.get('name'),
      contactName: formData.get('contactName') || null,
      contactEmail: formData.get('contactEmail') || null,
      contactPhone: formData.get('contactPhone') || null,
      billingTerms: formData.get('billingTerms') || 'net30',
      notes: formData.get('notes') || null,
    });
    const client = await createClient(auth.orgId, parsed);
    revalidatePath('/clients');
    return { ok: true, id: client.id };
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not create client.' };
  }
}

export async function updateClientAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  const clientId = String(formData.get('clientId'));
  try {
    const parsed = clientInput.parse({
      name: formData.get('name'),
      contactName: formData.get('contactName') || null,
      contactEmail: formData.get('contactEmail') || null,
      contactPhone: formData.get('contactPhone') || null,
      billingTerms: formData.get('billingTerms') || 'net30',
      notes: formData.get('notes') || null,
    });
    await updateClient(auth.orgId, clientId, parsed);
    revalidatePath('/clients');
    revalidatePath(`/clients/${clientId}`);
    return { ok: true, id: clientId };
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not update client.' };
  }
}

function parseChecklist(formData: FormData): ChecklistItemInput[] {
  const labels = formData.getAll('checklist_label').map(String);
  const areas = formData.getAll('checklist_area').map(String);
  const photos = formData.getAll('checklist_photo').map(String); // 'on' when checked
  const items: ChecklistItemInput[] = [];
  for (let i = 0; i < labels.length; i++) {
    const label = labels[i]?.trim();
    if (!label) continue;
    items.push({
      label,
      area: areas[i]?.trim() || null,
      requiresPhoto: photos[i] === 'true' || photos[i] === 'on',
      sortOrder: i,
    });
  }
  return items;
}

export async function createSiteAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  try {
    const parsed = siteInput.parse({
      clientId: formData.get('clientId'),
      name: formData.get('name'),
      address: formData.get('address') || null,
      lat: num(formData.get('lat')) ?? null,
      lng: num(formData.get('lng')) ?? null,
      geofenceRadiusM: num(formData.get('geofenceRadiusM')) ?? 150,
      siteType: formData.get('siteType') || 'office',
      squareFootage: num(formData.get('squareFootage')) ?? null,
      serviceFrequency: formData.get('serviceFrequency') || 'weekly',
      contractRateCents: num(formData.get('contractRateDollars')) != null
        ? Math.round((num(formData.get('contractRateDollars')) as number) * 100)
        : null,
      active: true,
      checklist: parseChecklist(formData),
    });
    const site = await createSite(auth.orgId, parsed);
    revalidatePath('/clients');
    revalidatePath(`/sites/${site.id}`);
    return { ok: true, id: site.id };
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not create site.' };
  }
}

export async function updateSiteAction(_prev: FormState, formData: FormData): Promise<FormState> {
  const auth = await requireOwner();
  const siteId = String(formData.get('siteId'));
  try {
    const parsed = siteInput.parse({
      clientId: formData.get('clientId'),
      name: formData.get('name'),
      address: formData.get('address') || null,
      lat: num(formData.get('lat')) ?? null,
      lng: num(formData.get('lng')) ?? null,
      geofenceRadiusM: num(formData.get('geofenceRadiusM')) ?? 150,
      siteType: formData.get('siteType') || 'office',
      squareFootage: num(formData.get('squareFootage')) ?? null,
      serviceFrequency: formData.get('serviceFrequency') || 'weekly',
      contractRateCents: num(formData.get('contractRateDollars')) != null
        ? Math.round((num(formData.get('contractRateDollars')) as number) * 100)
        : null,
      active: formData.get('active') !== 'false',
    });
    await updateSite(auth.orgId, siteId, parsed);
    await replaceChecklist(auth.orgId, siteId, parseChecklist(formData));
    revalidatePath(`/sites/${siteId}`);
    revalidatePath('/clients');
    return { ok: true, id: siteId };
  } catch (err) {
    return { error: err instanceof DomainError ? err.message : 'Could not update site.' };
  }
}
