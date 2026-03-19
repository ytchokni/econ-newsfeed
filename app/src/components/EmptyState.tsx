export default function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-lg bg-[var(--bg-card)] shadow-card p-10 text-center font-sans text-sm text-[var(--text-muted)]">
      {message}
    </div>
  );
}
