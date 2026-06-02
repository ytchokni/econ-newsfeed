import { useState } from "react";
import type { AdminDashboardData } from "@/lib/api";
import { useDeactivatedUrls, useAtRiskUrls, reactivateUrl } from "@/lib/api";

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

function ReasonBadge({ reason }: { reason: string }) {
  const colors: Record<string, string> = {
    consecutive_failures: "bg-red-900/50 text-red-400 border-red-800",
    response_too_large: "bg-amber-900/50 text-amber-400 border-amber-800",
  };
  const labels: Record<string, string> = {
    consecutive_failures: "Dead",
    response_too_large: "Too large",
  };
  const cls = colors[reason] || "bg-zinc-800 text-zinc-400 border-zinc-700";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {labels[reason] || reason}
    </span>
  );
}

function formatRelativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  if (diff < 0) {
    const mins = Math.floor(-diff / 60000);
    if (mins < 60) return `in ${mins}m`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `in ${hours}h`;
    const days = Math.floor(hours / 24);
    return `in ${days}d`;
  }
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function DeactivatedUrlsSection() {
  const { data: deactivated, mutate: mutateDeactivated } = useDeactivatedUrls();
  const { data: atRisk } = useAtRiskUrls();
  const [reactivating, setReactivating] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function handleReactivate(urlId: number) {
    setReactivating(urlId);
    setError(null);
    try {
      await reactivateUrl(urlId);
      mutateDeactivated();
    } catch {
      setError("Failed to re-activate URL");
    } finally {
      setReactivating(null);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="px-3 py-2 bg-red-900/20 border border-red-800/50 rounded text-sm text-red-400">
          {error}
        </div>
      )}
      {atRisk && atRisk.length > 0 && (
        <div className="px-3 py-2 bg-amber-900/20 border border-amber-800/50 rounded">
          <p className="text-sm text-amber-400 font-medium mb-2">
            {atRisk.length} URL{atRisk.length !== 1 ? "s" : ""} at risk of deactivation
          </p>
          <div className="space-y-1">
            {atRisk.map((u) => (
              <div key={u.id} className="flex items-center gap-2 text-xs text-zinc-400">
                <span className="text-amber-500">{u.consecutive_failures}/3</span>
                <span className="truncate flex-1">{u.url}</span>
                <span className="text-zinc-500">{u.researcher_name}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {deactivated && deactivated.length > 0 ? (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-zinc-500 border-b border-[#2a2d3a]">
                <th className="py-2 pr-3">URL</th>
                <th className="py-2 pr-3">Researcher</th>
                <th className="py-2 pr-3">Reason</th>
                <th className="py-2 pr-3">Since</th>
                <th className="py-2"></th>
              </tr>
            </thead>
            <tbody>
              {deactivated.map((u) => (
                <tr key={u.id} className="border-b border-[#2a2d3a]/50">
                  <td className="py-2 pr-3 text-zinc-300 max-w-xs truncate">{u.url}</td>
                  <td className="py-2 pr-3 text-zinc-400">{u.researcher_name}</td>
                  <td className="py-2 pr-3"><ReasonBadge reason={u.deactivation_reason} /></td>
                  <td className="py-2 pr-3 text-zinc-500">{u.deactivated_at ? formatRelativeTime(u.deactivated_at) : "—"}</td>
                  <td className="py-2 text-right">
                    <button
                      onClick={() => handleReactivate(u.id)}
                      disabled={reactivating === u.id}
                      className="px-2 py-1 text-xs text-emerald-400 hover:text-emerald-300 border border-emerald-800 rounded hover:bg-emerald-900/30 disabled:opacity-50"
                    >
                      {reactivating === u.id ? "..." : "Re-activate"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="text-sm text-zinc-500">No deactivated URLs</p>
      )}
    </div>
  );
}

export default function HealthTab({ data }: Props) {
  const { last_scrape, next_scrape_at, scrape_in_progress, total_researcher_urls, urls_by_page_type, deactivated_urls, at_risk_urls } = data;

  return (
    <div className="space-y-6">
      {/* Scrape status */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Scrape Status</h2>
        {scrape_in_progress && (
          <div className="mb-4 px-3 py-2 bg-blue-900/30 border border-blue-800 rounded text-sm text-blue-300">
            Scrape in progress...
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
          Researcher URLs <span className="text-zinc-600">({total_researcher_urls} active)</span>
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {Object.entries(urls_by_page_type).map(([type, count]) => (
            <div key={type} className="flex items-center justify-between px-3 py-2 bg-[#0f1117] rounded border border-[#2a2d3a]">
              <span className="text-sm text-zinc-300">{type.replace(/_/g, " ")}</span>
              <span className="text-sm font-medium text-zinc-100">{count as number}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Deactivated URLs */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">
          Deactivated URLs{" "}
          <span className="text-zinc-600">
            ({deactivated_urls} deactivated{at_risk_urls > 0 ? `, ${at_risk_urls} at risk` : ""})
          </span>
        </h2>
        <DeactivatedUrlsSection />
      </div>
    </div>
  );
}
