import { z } from 'zod';

/** Shared enums (kept in sync with the DB text columns in lib/db/schema.ts). */
export const siteTypeEnum = z.enum([
  'office',
  'medical',
  'retail',
  'school',
  'industrial',
  'other',
]);
export const frequencyEnum = z.enum(['daily', 'weekly', 'biweekly', 'monthly', 'custom']);
export const billingTermsEnum = z.enum(['net15', 'net30', 'due_on_receipt']);
export const roleEnum = z.enum(['owner', 'admin', 'cleaner']);
export const shiftStatusEnum = z.enum([
  'scheduled',
  'in_progress',
  'completed',
  'missed',
  'canceled',
]);
export const issueSeverityEnum = z.enum(['low', 'medium', 'high']);
export const issueSourceEnum = z.enum(['crew', 'client', 'ai']);
export const issueStatusEnum = z.enum(['open', 'acknowledged', 'resolved']);
export const invoiceStatusEnum = z.enum(['draft', 'sent', 'paid', 'overdue', 'void']);
export const quoteStatusEnum = z.enum(['draft', 'sent', 'won', 'lost']);

export const clientInput = z.object({
  name: z.string().min(1, 'Name is required'),
  contactName: z.string().optional().nullable(),
  contactEmail: z.string().email().optional().or(z.literal('')).nullable(),
  contactPhone: z.string().optional().nullable(),
  billingTerms: billingTermsEnum.default('net30'),
  notes: z.string().optional().nullable(),
});
export type ClientInput = z.infer<typeof clientInput>;

export const checklistItemInput = z.object({
  label: z.string().min(1),
  area: z.string().optional().nullable(),
  requiresPhoto: z.boolean().default(false),
  sortOrder: z.number().int().default(0),
});
export type ChecklistItemInput = z.infer<typeof checklistItemInput>;

export const siteInput = z.object({
  clientId: z.string().uuid(),
  name: z.string().min(1),
  address: z.string().optional().nullable(),
  lat: z.number().optional().nullable(),
  lng: z.number().optional().nullable(),
  geofenceRadiusM: z.number().int().positive().default(150),
  siteType: siteTypeEnum.default('office'),
  squareFootage: z.number().int().positive().optional().nullable(),
  serviceFrequency: frequencyEnum.default('weekly'),
  contractRateCents: z.number().int().nonnegative().optional().nullable(),
  active: z.boolean().default(true),
  checklist: z.array(checklistItemInput).optional(),
});
export type SiteInput = z.infer<typeof siteInput>;

export const shiftInput = z.object({
  siteId: z.string().uuid(),
  assignedUserId: z.string().uuid().optional().nullable(),
  scheduledStart: z.coerce.date(),
  scheduledEnd: z.coerce.date(),
});
export type ShiftInput = z.infer<typeof shiftInput>;

export const recurrenceInput = z.object({
  siteId: z.string().uuid(),
  rrule: z.string().min(1),
  defaultAssignedUserId: z.string().uuid().optional().nullable(),
  startTimeLocal: z
    .string()
    .regex(/^\d{2}:\d{2}$/, 'Use HH:MM')
    .default('18:00'),
  durationMinutes: z.number().int().positive().default(120),
  active: z.boolean().default(true),
});
export type RecurrenceInput = z.infer<typeof recurrenceInput>;
