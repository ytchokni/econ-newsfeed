import { Suspense } from "react";
import ResearchersContent from "./ResearchersContent";

export default function ResearchersPage() {
  return (
    <div className="max-w-[800px] mx-auto px-6 py-8">
      <h1 className="text-2xl font-bold text-[var(--ink)] mb-8">
        Tracked Researchers
      </h1>
      <Suspense>
        <ResearchersContent />
      </Suspense>
    </div>
  );
}
