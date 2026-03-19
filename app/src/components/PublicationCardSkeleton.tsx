export default function PublicationCardSkeleton() {
  return (
    <div className="rounded-lg bg-[var(--bg-card)] shadow-card p-5 animate-pulse">
      <div className="h-3 bg-[var(--border-light)] rounded-full w-20 mb-3" />
      <div className="h-5 bg-[var(--border)] rounded w-3/4" />
      <div className="mt-2.5 h-4 bg-[var(--border-light)] rounded w-1/3" />
      <div className="mt-2 h-4 bg-[var(--border-light)] rounded w-1/2" />
    </div>
  );
}
