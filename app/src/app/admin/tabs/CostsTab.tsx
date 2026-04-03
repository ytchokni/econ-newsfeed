import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["costs"];
}

function formatCost(usd: number): string {
  return `$${usd.toFixed(2)}`;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

export default function CostsTab({ data }: Props) {
  const { total_cost_usd, total_tokens, by_call_type, by_model, batch_vs_realtime, last_30_days } = data;

  return (
    <div className="space-y-6">
      {/* Totals */}
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Spend</p>
          <p className="text-2xl font-semibold text-zinc-100">{formatCost(total_cost_usd)}</p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Total Tokens</p>
          <p className="text-2xl font-semibold text-zinc-100">{formatTokens(total_tokens)}</p>
        </div>
        <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
          <p className="text-xs text-zinc-500 mb-1">Batch / Real-time</p>
          <p className="text-sm text-zinc-200">
            {formatCost(batch_vs_realtime.batch_cost)} / {formatCost(batch_vs_realtime.realtime_cost)}
          </p>
        </div>
      </div>

      {/* By call type */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Cost by Call Type</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
              <th className="text-left py-2 font-medium">Type</th>
              <th className="text-right py-2 font-medium">Calls</th>
              <th className="text-right py-2 font-medium">Tokens</th>
              <th className="text-right py-2 font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {by_call_type.map((row) => (
              <tr key={row.call_type} className="border-b border-[#2a2d3a] last:border-0">
                <td className="py-2 text-zinc-300">{row.call_type.replace(/_/g, " ")}</td>
                <td className="py-2 text-right text-zinc-300">{row.count.toLocaleString()}</td>
                <td className="py-2 text-right text-zinc-300">{formatTokens(row.tokens)}</td>
                <td className="py-2 text-right text-zinc-100 font-medium">{formatCost(row.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* By model */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Cost by Model</h2>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-zinc-500 border-b border-[#2a2d3a]">
              <th className="text-left py-2 font-medium">Model</th>
              <th className="text-right py-2 font-medium">Tokens</th>
              <th className="text-right py-2 font-medium">Cost</th>
            </tr>
          </thead>
          <tbody>
            {by_model.map((row) => (
              <tr key={row.model} className="border-b border-[#2a2d3a] last:border-0">
                <td className="py-2 text-zinc-300 font-mono text-xs">{row.model}</td>
                <td className="py-2 text-right text-zinc-300">{formatTokens(row.tokens)}</td>
                <td className="py-2 text-right text-zinc-100 font-medium">{formatCost(row.cost)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Daily trend (last 30 days) */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Daily Cost (Last 30 Days)</h2>
        {last_30_days.length === 0 ? (
          <p className="text-sm text-zinc-500">No data yet</p>
        ) : (
          <div className="space-y-1">
            {last_30_days.map((day) => {
              const maxCost = Math.max(...last_30_days.map((d) => d.cost), 0.01);
              const widthPct = Math.round((day.cost / maxCost) * 100);
              return (
                <div key={day.date} className="flex items-center gap-3">
                  <span className="text-xs text-zinc-500 w-20 shrink-0 font-mono">{day.date.slice(5)}</span>
                  <div className="flex-1 h-4 bg-[#0f1117] rounded overflow-hidden">
                    <div
                      className="h-full bg-[#4a9eff] rounded"
                      style={{ width: `${widthPct}%` }}
                    />
                  </div>
                  <span className="text-xs text-zinc-400 w-16 text-right shrink-0">{formatCost(day.cost)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
