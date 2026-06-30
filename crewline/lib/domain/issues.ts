import { and, desc, eq } from 'drizzle-orm';
import { db } from '@/lib/db';
import { issues, sites } from '@/lib/db/schema';
import { notFound } from './errors';
import { issueSeverityEnum, issueSourceEnum, issueStatusEnum } from './schemas';

export type CreateIssueInput = {
  siteId?: string | null;
  shiftId?: string | null;
  reportedByUserId?: string | null;
  source?: 'crew' | 'client' | 'ai';
  severity?: 'low' | 'medium' | 'high';
  description: string;
  aiSummary?: string | null;
};

export async function createIssue(orgId: string, input: CreateIssueInput) {
  const description = input.description?.trim();
  if (!description) notFound('Issue description');
  const rows = await db
    .insert(issues)
    .values({
      orgId,
      siteId: input.siteId ?? null,
      shiftId: input.shiftId ?? null,
      reportedByUserId: input.reportedByUserId ?? null,
      source: issueSourceEnum.parse(input.source ?? 'crew'),
      severity: issueSeverityEnum.parse(input.severity ?? 'medium'),
      status: 'open',
      description,
      aiSummary: input.aiSummary ?? null,
    })
    .returning();
  return rows[0];
}

export async function listIssues(orgId: string, status?: 'open' | 'acknowledged' | 'resolved') {
  const where = status
    ? and(eq(issues.orgId, orgId), eq(issues.status, status))
    : eq(issues.orgId, orgId);
  return db
    .select({ issue: issues, siteName: sites.name })
    .from(issues)
    .leftJoin(sites, eq(sites.id, issues.siteId))
    .where(where)
    .orderBy(desc(issues.createdAt));
}

export async function applyIssueTriage(
  orgId: string,
  issueId: string,
  triage: { severity: 'low' | 'medium' | 'high'; aiSummary: string },
) {
  const rows = await db
    .update(issues)
    .set({ severity: triage.severity, aiSummary: triage.aiSummary })
    .where(and(eq(issues.orgId, orgId), eq(issues.id, issueId)))
    .returning();
  return rows[0] ?? null;
}

export async function setIssueStatus(
  orgId: string,
  issueId: string,
  status: 'open' | 'acknowledged' | 'resolved',
) {
  const rows = await db
    .update(issues)
    .set({ status: issueStatusEnum.parse(status) })
    .where(and(eq(issues.orgId, orgId), eq(issues.id, issueId)))
    .returning();
  if (!rows[0]) notFound('Issue');
  return rows[0];
}
