import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["health"];
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

function formatRelativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export default function HealthTab({ data }: Props) {
  const { last_scrape, next_scrape_at, scrape_in_progress, total_researcher_urls, urls_by_page_type } = data;

  return (
    <div className="space-y-6">
      {/* Scrape status */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Scrape Status</h2>
        {scrape_in_progress && (
          <div className="mb-4 px-3 py-2 bg-blue-900/30 border border-blue-800 rounded text-sm text-blue-300">
            Scrape in progress…
          </div>
        )}
        {last_scrape ? (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-zinc-500 mb-1">Status</p>
              <StatusBadge status={last_scrape.status} />
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Last Run</p>
              <p className="text-sm text-zinc-200">{formatRelativeTime(last_scrape.started_at)}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">URLs Checked</p>
              <p className="text-sm text-zinc-200">{last_scrape.urls_checked}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">URLs Changed</p>
              <p className="text-sm text-zinc-200">{last_scrape.urls_changed}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Papers Extracted</p>
              <p className="text-sm text-zinc-200">{last_scrape.pubs_extracted}</p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Duration</p>
              <p className="text-sm text-zinc-200">
                {last_scrape.duration_seconds != null ? `${Math.floor(last_scrape.duration_seconds / 60)}m ${last_scrape.duration_seconds % 60}s` : "—"}
              </p>
            </div>
            <div>
              <p className="text-xs text-zinc-500 mb-1">Next Run</p>
              <p className="text-sm text-zinc-200">
                {next_scrape_at ? formatRelativeTime(next_scrape_at) : "—"}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-zinc-500">No scrapes recorded yet</p>
        )}
      </div>

      {/* URL counts */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">
          Researcher URLs <span className="text-zinc-600">({total_researcher_urls})</span>
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {Object.entries(urls_by_page_type).map(([type, count]) => (
            <div key={type} className="flex items-center justify-between px-3 py-2 bg-[#0f1117] rounded border border-[#2a2d3a]">
              <span className="text-sm text-zinc-300">{type.replace(/_/g, " ")}</span>
              <span className="text-sm font-medium text-zinc-100">{count}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
