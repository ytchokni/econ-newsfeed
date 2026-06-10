import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["extraction"];
}

const STALL_THRESHOLD_MS = 30 * 60 * 1000; // worker's worst healthy quiet period is the 10-min backoff

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

function formatRelativeTime(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

function isStalled(data: Props["data"]): boolean {
  if (!data.worker_enabled || data.queue.total === 0) return false;
  if (!data.last_call_at) return true;
  return Date.now() - new Date(data.last_call_at).getTime() > STALL_THRESHOLD_MS;
}

export default function ExtractionTab({ data }: Props) {
  const { queue, throughput, eta_days, tokens_last_24h, daily, recent_calls } = data;
  const stalled = isStalled(data);

  return (
    <div className="space-y-6">
      {/* Status row */}
      <div className="flex items-center gap-3 flex-wrap">
        <span
          className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${
            data.worker_enabled
              ? "bg-emerald-900/50 text-emerald-400 border-emerald-800"
              : "bg-zinc-800 text-zinc-400 border-zinc-700"
          }`}
        >
          {data.worker_enabled ? "Worker enabled" : "Worker disabled"}
        </span>
        {stalled && (
          <span className="inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border bg-red-900/50 text-red-400 border-red-800">
            Possibly stalled — no LLM call in 30+ min
          </span>
        )}
        <span className="text-xs text-zinc-500">
          Last call: {data.last_call_at ? formatRelativeTime(data.last_call_at) : "—"}
          {" · "}
          Last extraction: {data.last_extracted_at ? formatRelativeTime(data.last_extracted_at) : "—"}
        </span>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4">
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Queue</p>
          <p className="text-2xl font-semibold text-zinc-100">{queue.total.toLocaleString()}</p>
          <p className="text-xs text-zinc-500 mt-1">
            {queue.never_extracted.toLocaleString()} never · {queue.changed_pending.toLocaleString()} changed
          </p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Extracted (24h)</p>
          <p className="text-2xl font-semibold text-zinc-100">
            {throughput.completions.last_24h.toLocaleString()}
          </p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">ETA to drain</p>
          <p className="text-2xl font-semibold text-zinc-100">
            {eta_days !== null ? `~${eta_days % 1 === 0 ? eta_days.toFixed(0) : eta_days} days` : "—"}
          </p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Tokens (24h)</p>
          <p className="text-2xl font-semibold text-zinc-100">{formatTokens(tokens_last_24h)}</p>
        </div>
      </div>

      {/* Throughput */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Throughput</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
              <th className="text-left py-2 font-medium">Window</th>
              <th className="text-right py-2 font-medium">Completed</th>
              <th className="text-right py-2 font-medium">Attempts</th>
            </tr>
          </thead>
          <tbody>
            {(["last_hour", "last_24h", "last_7d"] as const).map((w) => (
              <tr key={w} className="border-b border-[#2a2d3a] last:border-0">
                <td className="py-2 text-zinc-300">
                  {w === "last_hour" ? "Last hour" : w === "last_24h" ? "Last 24h" : "Last 7 days"}
                </td>
                <td className="py-2 text-right text-zinc-100 font-medium">
                  {throughput.completions[w].toLocaleString()}
                </td>
                <td className="py-2 text-right text-zinc-300">
                  {throughput.attempts[w].toLocaleString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="text-xs text-zinc-500 mt-2">
          Attempts − completed ≈ failed/retried calls (quota exhaustion shows up here).
        </p>
      </div>

      {/* Daily trend */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Extractions per Day (14d)</h2>
        {daily.length === 0 ? (
          <p className="text-sm text-zinc-500">No extractions in the last 14 days.</p>
        ) : (
          <table className="w-full text-sm">
            <tbody>
              {daily.map((d) => (
                <tr key={d.date} className="border-b border-[#2a2d3a] last:border-0">
                  <td className="py-1.5 text-zinc-400">{d.date}</td>
                  <td className="py-1.5 text-right text-zinc-100">{d.count.toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Recent calls */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Recent Extraction Calls</h2>
        {recent_calls.length === 0 ? (
          <p className="text-sm text-zinc-500">No extraction calls recorded.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
                  <th className="text-left py-2 font-medium">When</th>
                  <th className="text-left py-2 font-medium">URL</th>
                  <th className="text-left py-2 font-medium">Model</th>
                  <th className="text-right py-2 font-medium">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {recent_calls.map((c, i) => (
                  <tr key={`${c.called_at}-${i}`} className="border-b border-[#2a2d3a]/50 last:border-0">
                    <td className="py-2 pr-3 text-zinc-500 whitespace-nowrap">
                      {formatRelativeTime(c.called_at)}
                    </td>
                    <td className="py-2 pr-3 text-zinc-300 max-w-md truncate">
                      {c.context_url ?? "—"}
                    </td>
                    <td className="py-2 pr-3 text-zinc-400">{c.model}</td>
                    <td className="py-2 text-right text-zinc-300">{formatTokens(c.total_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
