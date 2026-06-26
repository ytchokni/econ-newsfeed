export default function EmptyState({ message, onClear }: { message?: string; onClear?: () => void }) {
  return (
    <div className="text-center py-20 px-4">
      <p className="m-0 font-serif italic text-lg text-[var(--muted)]">
        {message || "No updates match your filters."}
      </p>
      {onClear && (
        <button
          onClick={onClear}
          className="mt-3.5 text-[13px] text-[var(--accent)] bg-transparent border-none cursor-pointer"
        >
          Clear filters
        </button>
      )}
    </div>
  );
}
