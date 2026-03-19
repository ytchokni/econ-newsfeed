import ResearchersContent from "./ResearchersContent";

export default function ResearchersPage() {
  return (
    <div>
      <h1 className="font-serif text-2xl font-bold text-[var(--text-primary)] mb-8">
        Tracked Researchers
      </h1>
      <ResearchersContent />
    </div>
  );
}
