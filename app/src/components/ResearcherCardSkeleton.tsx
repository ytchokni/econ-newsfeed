export default function ResearcherCardSkeleton() {
  return (
    <div className="border-b border-[var(--line)] py-[18px] animate-pulse">
      <div className="flex items-baseline justify-between gap-4">
        <div className="flex items-baseline gap-2 flex-1">
          <div className="h-5 bg-[var(--line)] rounded-sm w-36" />
          <div className="h-4 bg-[var(--line)] rounded-sm w-48 opacity-60" />
        </div>
        <div className="flex items-center gap-4">
          <div className="h-4 bg-[var(--line)] rounded-sm w-16 opacity-40" />
          <div className="h-7 bg-[var(--line)] rounded-sm w-16 opacity-40" />
        </div>
      </div>
    </div>
  );
}
