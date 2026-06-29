"use client";

import { useState } from "react";
import { useReviews } from "@/lib/api";
import type { ReviewItem } from "@/lib/api";

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-4">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-xl font-semibold text-zinc-100">
        {typeof value === "number" ? value.toLocaleString() : value}
      </p>
    </div>
  );
}

function CorrectionBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    update_status: "bg-blue-500/20 text-blue-300",
    update_venue: "bg-amber-500/20 text-amber-300",
    hide_event: "bg-red-500/20 text-red-300",
  };
  const labels: Record<string, string> = {
    update_status: "Status",
    update_venue: "Venue",
    hide_event: "Hidden",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs font-medium ${colors[type] || "bg-zinc-500/20 text-zinc-300"}`}>
      {labels[type] || type}
    </span>
  );
}

function ReviewRow({ item }: { item: ReviewItem }) {
  const [expanded, setExpanded] = useState(false);
  const corrections = item.corrections_applied || [];
  const issues = item.issues || [];
  const highIssues = issues.filter((i) => i.severity === "high");

  return (
    <>
      <tr
        className="border-b border-[#2a2d3a] hover:bg-[#1a1d27] cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="py-3 px-4 text-sm text-zinc-300 max-w-md truncate">
          {item.paper_title}
        </td>
        <td className="py-3 px-4 text-sm text-zinc-400">{item.event_type}</td>
        <td className="py-3 px-4 text-sm">
          <span className="text-zinc-400">{issues.length}</span>
          {highIssues.length > 0 && (
            <span className="ml-1 text-red-400">({highIssues.length} high)</span>
          )}
        </td>
        <td className="py-3 px-4">
          <div className="flex gap-1 flex-wrap">
            {corrections.map((c, i) => (
              <CorrectionBadge key={i} type={c.type} />
            ))}
            {corrections.length === 0 && (
              <span className="text-xs text-zinc-600">none</span>
            )}
          </div>
        </td>
        <td className="py-3 px-4 text-sm text-zinc-500">
          {new Date(item.reviewed_at).toLocaleDateString()}
        </td>
      </tr>
      {expanded && (
        <tr className="border-b border-[#2a2d3a]">
          <td colSpan={5} className="px-4 py-3 bg-[#141620]">
            {issues.length > 0 ? (
              <div className="space-y-2">
                {issues.map((issue, i) => (
                  <div key={i} className="text-sm">
                    <span className={`font-medium ${issue.severity === "high" ? "text-red-400" : issue.severity === "medium" ? "text-amber-400" : "text-zinc-400"}`}>
                      [{issue.severity}] {issue.type}
                    </span>
                    <span className="text-zinc-400 ml-2">{issue.description}</span>
                    {issue.correction && (
                      <span className="text-emerald-400 ml-2">
                        correction: {issue.correction}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-sm text-zinc-500">No issues found</p>
            )}
            {corrections.length > 0 && (
              <div className="mt-3 pt-3 border-t border-[#2a2d3a]">
                <p className="text-xs text-zinc-500 mb-2">Applied corrections:</p>
                {corrections.map((c, i) => (
                  <div key={i} className="text-sm text-zinc-300">
                    <CorrectionBadge type={c.type} />
                    <span className="ml-2">
                      {c.old_value ?? "(empty)"} <span className="text-zinc-500">&rarr;</span> {c.new_value ?? "(cleared)"}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

const PAGE_SIZE = 50;

export default function ReviewsTab() {
  const [correctionsOnly, setCorrectionsOnly] = useState(true);
  const [page, setPage] = useState(0);

  const { data, isLoading } = useReviews({
    has_corrections: correctionsOnly,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
  });

  const stats = data?.stats;

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Events Reviewed" value={stats?.total_reviewed ?? 0} />
        <StatCard label="With Issues" value={stats?.total_with_issues ?? 0} />
        <StatCard label="Corrections Applied" value={stats?.total_corrections ?? 0} />
        <StatCard
          label="By Type"
          value={
            stats?.corrections_by_type
              ? Object.entries(stats.corrections_by_type)
                  .map(([k, v]) => `${k.replace("update_", "").replace("hide_event", "hidden")}: ${v}`)
                  .join(", ") || "none"
              : "..."
          }
        />
      </div>

      {/* Filter */}
      <div className="flex items-center gap-4">
        <label className="flex items-center gap-2 text-sm text-zinc-400">
          <input
            type="checkbox"
            checked={correctionsOnly}
            onChange={(e) => {
              setCorrectionsOnly(e.target.checked);
              setPage(0);
            }}
            className="rounded border-[#2a2d3a] bg-[#1a1d27]"
          />
          Corrections only
        </label>
        <span className="text-sm text-zinc-500">
          {data?.total ?? 0} results
        </span>
      </div>

      {/* Table */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-[#2a2d3a] text-left">
              <th className="py-3 px-4 text-xs font-medium text-zinc-500 uppercase">Title</th>
              <th className="py-3 px-4 text-xs font-medium text-zinc-500 uppercase">Event</th>
              <th className="py-3 px-4 text-xs font-medium text-zinc-500 uppercase">Issues</th>
              <th className="py-3 px-4 text-xs font-medium text-zinc-500 uppercase">Corrections</th>
              <th className="py-3 px-4 text-xs font-medium text-zinc-500 uppercase">Date</th>
            </tr>
          </thead>
          <tbody>
            {isLoading && !data ? (
              <tr>
                <td colSpan={5} className="py-8 text-center text-zinc-500">
                  Loading...
                </td>
              </tr>
            ) : data?.items.length === 0 ? (
              <tr>
                <td colSpan={5} className="py-8 text-center text-zinc-500">
                  No reviews yet
                </td>
              </tr>
            ) : (
              data?.items.map((item) => (
                <ReviewRow key={item.id} item={item} />
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {data && data.total > PAGE_SIZE && (
        <div className="flex items-center justify-between">
          <button
            onClick={() => setPage((p) => Math.max(0, p - 1))}
            disabled={page === 0}
            className="px-3 py-1.5 text-sm rounded bg-[#1a1d27] border border-[#2a2d3a] text-zinc-400 disabled:opacity-30"
          >
            Previous
          </button>
          <span className="text-sm text-zinc-500">
            Page {page + 1} of {Math.ceil(data.total / PAGE_SIZE)}
          </span>
          <button
            onClick={() => setPage((p) => p + 1)}
            disabled={(page + 1) * PAGE_SIZE >= data.total}
            className="px-3 py-1.5 text-sm rounded bg-[#1a1d27] border border-[#2a2d3a] text-zinc-400 disabled:opacity-30"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
