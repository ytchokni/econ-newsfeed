export default function ResearcherCardSkeleton() {
  return (
    <div className="border-b border-[var(--line)] py-[var(--rowpad)] animate-pulse">
      <div className="h-5 bg-[var(--line)] rounded-sm w-1/3" />
      <div className="mt-2.5 h-4 bg-[var(--line)] rounded-sm w-2/3 opacity-60" />
      <div className="mt-2 h-4 bg-[var(--line)] rounded-sm w-1/4 opacity-40" />
    </div>
  );
}
