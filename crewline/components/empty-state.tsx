export function EmptyState({
  icon,
  title,
  body,
  action,
}: {
  icon?: React.ReactNode;
  title: string;
  body?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] py-12 text-center">
      {icon && <div className="text-[var(--color-muted-foreground)]">{icon}</div>}
      <h3 className="font-medium">{title}</h3>
      {body && <p className="max-w-sm text-sm text-[var(--color-muted-foreground)]">{body}</p>}
      {action && <div className="mt-2 text-sm">{action}</div>}
    </div>
  );
}
