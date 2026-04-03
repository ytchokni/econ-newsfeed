import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["content"];
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-2xl font-semibold text-zinc-100">{value.toLocaleString()}</p>
    </div>
  );
}

export default function ContentTab({ data }: Props) {
  const { total_papers, total_researchers, papers_by_status, papers_by_year, researchers_by_position } = data;

  return (
    <div className="space-y-6">
      {/* Big numbers */}
      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Total Papers" value={total_papers} />
        <StatCard label="Total Researchers" value={total_researchers} />
      </div>

      {/* Papers by status */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Papers by Status</h2>
        <div className="space-y-2">
          {Object.entries(papers_by_status).map(([status, count]) => (
            <div key={status} className="flex items-center justify-between py-1.5 border-b border-[#2a2d3a] last:border-0">
              <span className="text-sm text-zinc-300">{status.replace(/_/g, " ")}</span>
              <span className="text-sm font-medium text-zinc-100">{count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Papers by year */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Papers by Year</h2>
        <div className="space-y-2">
          {papers_by_year.map(({ year, count }) => (
            <div key={year} className="flex items-center justify-between py-1.5 border-b border-[#2a2d3a] last:border-0">
              <span className="text-sm text-zinc-300">{year}</span>
              <span className="text-sm font-medium text-zinc-100">{count.toLocaleString()}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Researchers by position */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Researchers by Position</h2>
        <div className="space-y-2">
          {Object.entries(researchers_by_position).map(([position, count]) => (
            <div key={position} className="flex items-center justify-between py-1.5 border-b border-[#2a2d3a] last:border-0">
              <span className="text-sm text-zinc-300">{position}</span>
              <span className="text-sm font-medium text-zinc-100">{count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
