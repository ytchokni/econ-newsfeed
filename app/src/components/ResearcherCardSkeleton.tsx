export default function ResearcherCardSkeleton() {
  return (
    <div className="rounded-lg bg-[var(--bg-card)] shadow-card p-5 animate-pulse">
      <div className="h-5 bg-[var(--border)] rounded w-1/2" />
      <div className="mt-2.5 h-4 bg-[var(--border-light)] rounded w-2/3" />
      <div className="mt-2 h-4 bg-[var(--border-light)] rounded w-1/4" />
    </div>
  );
}
