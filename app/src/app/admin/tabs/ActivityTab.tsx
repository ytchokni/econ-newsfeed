import type { AdminDashboardData } from "@/lib/api";

interface Props {
  data: AdminDashboardData["activity"];
}

function EventBadge({ type }: { type: string }) {
  const colors: Record<string, string> = {
    new_paper: "bg-emerald-900/50 text-emerald-400 border-emerald-800",
    status_change: "bg-amber-900/50 text-amber-400 border-amber-800",
    title_change: "bg-purple-900/50 text-purple-400 border-purple-800",
  };
  const cls = colors[type] || "bg-zinc-800 text-zinc-400 border-zinc-700";
  return (
    <span className={`inline-flex items-center px-2 py-0.5 text-xs font-medium rounded border ${cls}`}>
      {type.replace(/_/g, " ")}
    </span>
  );
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })
    + " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function EventCountCard({ label, counts }: { label: string; counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  return (
    <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
      <div className="flex items-center justify-between mb-3">
        <p className="text-xs text-zinc-500">{label}</p>
        <p className="text-lg font-semibold text-zinc-100">{total}</p>
      </div>
      <div className="space-y-1">
        {Object.entries(counts).map(([type, count]) => (
          <div key={type} className="flex items-center justify-between text-xs">
            <span className="text-zinc-400">{type.replace(/_/g, " ")}</span>
            <span className="text-zinc-300">{count}</span>
          </div>
        ))}
        {Object.keys(counts).length === 0 && (
          <p className="text-xs text-zinc-600">No events</p>
        )}
      </div>
    </div>
  );
}

export default function ActivityTab({ data }: Props) {
  const { events_last_7d, events_last_30d, recent_events } = data;

  return (
    <div className="space-y-6">
      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4">
        <EventCountCard label="Last 7 Days" counts={events_last_7d} />
        <EventCountCard label="Last 30 Days" counts={events_last_30d} />
      </div>

      {/* Recent events */}
      <div className="bg-[#1a1d27] rounded-lg border border-[#2a2d3a] p-5">
        <h2 className="text-sm font-medium text-zinc-400 mb-4">Recent Events</h2>
        <div className="space-y-3">
          {recent_events.map((event, i) => (
            <div
              key={i}
              className="flex items-start gap-3 py-2 border-b border-[#2a2d3a] last:border-0"
            >
              <EventBadge type={event.event_type} />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-zinc-200 truncate">{event.paper_title}</p>
                {event.details && (
                  <p className="text-xs text-zinc-500 mt-0.5">{event.details}</p>
                )}
              </div>
              <span className="text-xs text-zinc-600 shrink-0">{formatDate(event.created_at)}</span>
            </div>
          ))}
          {recent_events.length === 0 && (
            <p className="text-sm text-zinc-500 text-center py-4">No events yet</p>
          )}
        </div>
      </div>
    </div>
  );
}
