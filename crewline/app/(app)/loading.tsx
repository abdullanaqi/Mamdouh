export default function Loading() {
  return (
    <div className="space-y-4">
      <div className="h-7 w-40 animate-pulse rounded bg-[var(--color-muted)]" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-24 animate-pulse rounded-xl bg-[var(--color-muted)]" />
        ))}
      </div>
      <div className="h-64 animate-pulse rounded-xl bg-[var(--color-muted)]" />
    </div>
  );
}
