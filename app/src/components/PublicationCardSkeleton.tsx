export default function PublicationCardSkeleton() {
  return (
    <div className="py-[var(--rowpad)] border-b border-[var(--line)] animate-pulse">
      <div className="h-5 bg-[var(--line)] rounded w-3/4" />
      <div className="mt-2.5 h-4 bg-[var(--line)] rounded w-1/3" />
      <div className="mt-3 flex gap-3">
        <div className="h-5 bg-[var(--line)] rounded w-20" />
        <div className="h-5 bg-[var(--line)] rounded w-16" />
      </div>
    </div>
  );
}
