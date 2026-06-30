import Link from 'next/link';

export default function NotFound() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[var(--color-muted)] p-6 text-center">
      <h1 className="text-3xl font-bold">404</h1>
      <p className="text-[var(--color-muted-foreground)]">This page could not be found.</p>
      <Link href="/" className="text-[var(--color-primary)] hover:underline">
        Back to home
      </Link>
    </main>
  );
}
