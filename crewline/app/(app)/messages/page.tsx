import { DateTime } from 'luxon';
import { requireOwner } from '@/lib/auth/context';
import { listIssues } from '@/lib/domain/issues';
import { listMessages } from '@/lib/domain/messages';
import { getOrgTimezone } from '@/lib/domain/org';
import { setIssueStatusAction } from '@/app/actions/issues';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/empty-state';
import { MessageReplyButton } from '@/components/messages/reply-button';
import { aiEnabled } from '@/lib/ai/enabled';

export default async function MessagesPage() {
  const auth = await requireOwner();
  const tz = await getOrgTimezone(auth.orgId);
  const [issues, messages] = await Promise.all([
    listIssues(auth.orgId),
    listMessages(auth.orgId),
  ]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Messages & Issues</h1>

      <Card>
        <CardHeader>
          <CardTitle>Issues</CardTitle>
        </CardHeader>
        <CardContent>
          {issues.length === 0 ? (
            <EmptyState title="No issues" body="Crew and client issues will appear here." />
          ) : (
            <ul className="space-y-3">
              {issues.map(({ issue, siteName }) => (
                <li key={issue.id} className="rounded-lg border border-[var(--color-border)] p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <p className="font-medium">{issue.description}</p>
                      <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                        {siteName ?? 'No site'} · {issue.source} ·{' '}
                        {DateTime.fromJSDate(issue.createdAt, { zone: tz }).toFormat('LLL d, h:mm a')}
                      </p>
                      {issue.aiSummary && (
                        <p className="mt-1 text-xs italic text-[var(--color-muted-foreground)]">
                          AI: {issue.aiSummary}
                        </p>
                      )}
                    </div>
                    <div className="flex shrink-0 flex-col items-end gap-2">
                      <Badge variant={issue.severity === 'high' ? 'destructive' : issue.severity === 'medium' ? 'warning' : 'secondary'}>
                        {issue.severity}
                      </Badge>
                      <Badge variant="outline">{issue.status}</Badge>
                    </div>
                  </div>
                  <div className="mt-2 flex gap-2">
                    {issue.status !== 'acknowledged' && issue.status !== 'resolved' && (
                      <form action={setIssueStatusAction}>
                        <input type="hidden" name="issueId" value={issue.id} />
                        <input type="hidden" name="status" value="acknowledged" />
                        <Button type="submit" size="sm" variant="outline">
                          Acknowledge
                        </Button>
                      </form>
                    )}
                    {issue.status !== 'resolved' && (
                      <form action={setIssueStatusAction}>
                        <input type="hidden" name="issueId" value={issue.id} />
                        <input type="hidden" name="status" value="resolved" />
                        <Button type="submit" size="sm">
                          Resolve
                        </Button>
                      </form>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Client messages</CardTitle>
        </CardHeader>
        <CardContent>
          {messages.length === 0 ? (
            <EmptyState title="No messages" body="Client messages and your replies will appear here." />
          ) : (
            <ul className="space-y-3">
              {messages.map(({ message, clientName }) => (
                <li key={message.id} className="rounded-lg border border-[var(--color-border)] p-3">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-medium">{clientName ?? 'Unknown client'}</span>
                    <Badge variant={message.direction === 'inbound' ? 'secondary' : 'outline'}>
                      {message.direction}
                    </Badge>
                  </div>
                  <p className="mt-1 text-sm">{message.body}</p>
                  <p className="mt-1 text-xs text-[var(--color-muted-foreground)]">
                    {message.channel} ·{' '}
                    {DateTime.fromJSDate(message.createdAt, { zone: tz }).toFormat('LLL d, h:mm a')}
                    {message.aiGenerated ? ' · AI draft' : ''}
                  </p>
                  {message.direction === 'inbound' && (
                    <div className="mt-2">
                      <MessageReplyButton messageId={message.id} aiEnabled={aiEnabled()} />
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
