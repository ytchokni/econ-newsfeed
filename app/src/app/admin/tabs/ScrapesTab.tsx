import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["scrapes"];
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    completed: "bg-emerald-900/50 text-emerald-400 border-emerald-800",
    running: "bg-blue-900/50 text-blue-400 border-blue-800",
    failed: "bg-red-900/50 text-red-400 border-red-800",
  };
  const cls = colors[status] || "bg-zinc-800 text-zinc-400 border-zinc-700";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {status}
    </span>
  );
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "—";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export default function ScrapesTab({ data }: Props) {
  const { recent, totals } = data;

  return (
    <div className="space-y-6">
      {/* Totals */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Scrapes</p>
          <p className="text-2xl font-semibold text-zinc-100">{totals.total_scrapes.toLocaleString()}</p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Papers Extracted</p>
          <p className="text-2xl font-semibold text-zinc-100">{totals.total_pubs_extracted.toLocaleString()}</p>
        </div>
      </div>

      {/* Recent scrapes table */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5 overflow-x-auto">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Recent Scrapes</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
              <th className="text-left py-2 font-medium">Date</th>
              <th className="text-left py-2 font-medium">Status</th>
              <th className="text-right py-2 font-medium">Checked</th>
              <th className="text-right py-2 font-medium">Changed</th>
              <th className="text-right py-2 font-medium">Extracted</th>
              <th className="text-right py-2 font-medium">Tokens</th>
              <th className="text-right py-2 font-medium">Duration</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((row, i) => (
              <tr key={i} className="border-b border-[#2a2d3a] last:border-0">
                <td className="py-2 text-zinc-300 font-mono text-xs">{formatDate(row.started_at)}</td>
                <td className="py-2"><StatusBadge status={row.status} /></td>
                <td className="py-2 text-right text-zinc-300">{row.urls_checked}</td>
                <td className="py-2 text-right text-zinc-300">{row.urls_changed}</td>
                <td className="py-2 text-right text-zinc-100 font-medium">{row.pubs_extracted}</td>
                <td className="py-2 text-right text-zinc-300">{row.tokens_used.toLocaleString()}</td>
                <td className="py-2 text-right text-zinc-300">{formatDuration(row.duration_seconds)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {recent.length === 0 && (
          <p className="text-sm text-zinc-500 py-4 text-center">No scrapes recorded yet</p>
        )}
      </div>
    </div>
  );
}
