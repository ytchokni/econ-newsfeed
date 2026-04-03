import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["quality"];
  totalPapers: number;
  totalResearchers: number;
}

function CoverageBar({
  label,
  count,
  total,
}: {
  label: string;
  count: number;
  total: number;
}) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0;
  const barColor = pct >= 75 ? "bg-emerald-500" : pct >= 40 ? "bg-amber-500" : "bg-red-500";

  return (
    <div className="py-3 border-b border-[#2a2d3a] last:border-0">
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-sm text-zinc-300">{label}</span>
        <span className="text-sm text-zinc-400">
          {count.toLocaleString()} / {total.toLocaleString()}{" "}
          <span className="font-medium text-zinc-200">({pct}%)</span>
        </span>
      </div>
      <div className="h-1.5 bg-[#0f1117] rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full ${barColor}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function QualityTab({ data, totalPapers, totalResearchers }: Props) {
  return (
    <div className="space-y-6">
      {/* Paper data quality */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Paper Data Coverage</h2>
        <CoverageBar label="Has Abstract" count={data.papers_with_abstract} total={totalPapers} />
        <CoverageBar label="Has DOI" count={data.papers_with_doi} total={totalPapers} />
        <CoverageBar label="OpenAlex Enriched" count={data.papers_with_openalex} total={totalPapers} />
        <CoverageBar label="Has Draft URL" count={data.papers_with_draft_url} total={totalPapers} />
        <CoverageBar label="Draft URL Valid" count={data.draft_url_valid} total={data.papers_with_draft_url || 1} />
      </div>

      {/* Researcher data quality */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Researcher Data Coverage</h2>
        <CoverageBar label="Has Description" count={data.researchers_with_description} total={totalResearchers} />
        <CoverageBar label="JEL Classified" count={data.researchers_with_jel} total={totalResearchers} />
        <CoverageBar label="OpenAlex ID" count={data.researchers_with_openalex_id} total={totalResearchers} />
      </div>
    </div>
  );
}
