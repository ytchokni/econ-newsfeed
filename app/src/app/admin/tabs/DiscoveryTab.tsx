"use client";

import { useState } from "react";
import type { AdminDashboardData } from "@/lib/api";
import {
  useDiscoveries,
  approveDiscovery,
  rejectDiscovery,
  bulkApproveDiscoveries,
} from "@/lib/api";

interface Props {
  data: AdminDashboardData["discovery"];
  onMutate: () => void;
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-4">
      <p className="text-xs text-zinc-500 mb-1">{label}</p>
      <p className="text-xl font-semibold text-zinc-100">
        {value.toLocaleString()}
      </p>
    </div>
  );
}

export default function DiscoveryTab({ data, onMutate }: Props) {
  const { data: discoveries, mutate } = useDiscoveries();
  const [acting, setActing] = useState<number | null>(null);
  const [bulkActing, setBulkActing] = useState(false);

  async function handleApprove(id: number) {
    setActing(id);
    try {
      await approveDiscovery(id);
      mutate();
      onMutate();
    } finally {
      setActing(null);
    }
  }

  async function handleReject(id: number) {
    setActing(id);
    try {
      await rejectDiscovery(id);
      mutate();
      onMutate();
    } finally {
      setActing(null);
    }
  }

  async function handleBulkApprove() {
    setBulkActing(true);
    try {
      await bulkApproveDiscoveries();
      mutate();
      onMutate();
    } finally {
      setBulkActing(false);
    }
  }

  const pending = discoveries?.pending || [];
  const recent = discoveries?.recent || [];
  const highConfCount = pending.filter(
    (d) => d.confidence !== null && d.confidence >= 0.8
  ).length;

  return (
    <div className="space-y-6">
      {/* Stats */}
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        <StatCard label="Pool remaining" value={data.pool_remaining} />
        <StatCard label="Searched" value={data.total_searched} />
        <StatCard label="Pending" value={data.pending_review} />
        <StatCard label="Approved" value={data.approved} />
        <StatCard label="Rejected" value={data.rejected} />
        <StatCard label="No result" value={data.no_result} />
      </div>

      {/* Pending review */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-medium text-zinc-400">
            Pending Review ({pending.length})
          </h2>
          {highConfCount > 0 && (
            <button
              onClick={handleBulkApprove}
              disabled={bulkActing}
              className="px-3 py-1.5 text-xs font-medium rounded bg-emerald-900/50 text-emerald-400 border border-emerald-800 hover:bg-emerald-900/80 disabled:opacity-50"
            >
              {bulkActing
                ? "Approving..."
                : `Approve all high-confidence (${highConfCount})`}
            </button>
          )}
        </div>

        {pending.length === 0 ? (
          <p className="text-sm text-zinc-500">No discoveries pending review.</p>
        ) : (
          <div className="space-y-3">
            {pending.map((d) => (
              <div
                key={d.id}
                className="flex items-start gap-4 p-3 rounded border border-[#2a2d3a] bg-[#0f1117]"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium text-zinc-200">
                      {d.first_name} {d.last_name}
                    </span>
                    {d.affiliation && (
                      <span className="text-xs text-zinc-500">
                        {d.affiliation}
                      </span>
                    )}
                    {d.confidence !== null && (
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          d.confidence >= 0.8
                            ? "bg-emerald-900/50 text-emerald-400"
                            : d.confidence >= 0.5
                              ? "bg-amber-900/50 text-amber-400"
                              : "bg-red-900/50 text-red-400"
                        }`}
                      >
                        {(d.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>
                  {d.url && (
                    <a
                      href={d.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-sm text-blue-400 hover:text-blue-300 break-all"
                    >
                      {d.url}
                    </a>
                  )}
                  {d.subpages && d.subpages.length > 0 && (
                    <div className="mt-1 flex gap-2 flex-wrap">
                      {d.subpages.map((sp, i) => (
                        <a
                          key={i}
                          href={sp.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 hover:text-zinc-300"
                        >
                          {sp.page_type}
                        </a>
                      ))}
                    </div>
                  )}
                  <a
                    href={`https://www.google.com/search?q=${encodeURIComponent(d.search_query)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-zinc-600 hover:text-zinc-400 mt-1 inline-block"
                  >
                    verify search
                  </a>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button
                    onClick={() => handleApprove(d.id)}
                    disabled={acting === d.id}
                    className="px-2.5 py-1 text-xs rounded bg-emerald-900/50 text-emerald-400 border border-emerald-800 hover:bg-emerald-900/80 disabled:opacity-50"
                  >
                    Approve
                  </button>
                  <button
                    onClick={() => handleReject(d.id)}
                    disabled={acting === d.id}
                    className="px-2.5 py-1 text-xs rounded bg-red-900/50 text-red-400 border border-red-800 hover:bg-red-900/80 disabled:opacity-50"
                  >
                    Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Recent history */}
      {recent.length > 0 && (
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <h2 className="text-sm font-medium text-zinc-400 mb-4">
            Recent Reviews
          </h2>
          <div className="space-y-2">
            {recent.map((d) => (
              <div
                key={d.id}
                className="flex items-center gap-3 py-2 border-b border-[#2a2d3a] last:border-0 text-sm"
              >
                <span
                  className={`text-xs px-1.5 py-0.5 rounded ${
                    d.status === "approved"
                      ? "bg-emerald-900/50 text-emerald-400"
                      : "bg-red-900/50 text-red-400"
                  }`}
                >
                  {d.status}
                </span>
                <span className="text-zinc-300">
                  {d.first_name} {d.last_name}
                </span>
                {d.url && (
                  <a
                    href={d.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-zinc-500 hover:text-zinc-400 truncate max-w-xs"
                  >
                    {d.url}
                  </a>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
