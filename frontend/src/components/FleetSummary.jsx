import { Activity, Cpu, Layers, Clock, Server } from 'lucide-react'

/** Headline numbers. First thing the eye lands on, so it has to be readable cold. */
export default function FleetSummary({ summary }) {
  if (!summary) return null

  const tiles = [
    {
      label: 'QPUs online',
      value: `${summary.online_backends}/${summary.total_backends}`,
      icon: Server,
      accent: 'text-signal-green',
      detail:
        summary.paused_backends + summary.offline_backends > 0
          ? `${summary.paused_backends + summary.offline_backends} calibrating`
          : 'full fleet available',
    },
    {
      label: 'Qubits available',
      value: summary.total_qubits_available.toLocaleString(),
      icon: Cpu,
      accent: 'text-signal-cyan',
      detail: 'on operational hardware',
    },
    {
      label: 'Jobs queued fleet-wide',
      value: summary.total_queued_jobs.toLocaleString(),
      icon: Layers,
      accent: 'text-signal-violet',
      detail: summary.busiest_backend ? `busiest ${summary.busiest_backend}` : 'fleet idle',
    },
    {
      label: 'Median queue depth',
      value: summary.median_queue_depth,
      icon: Clock,
      accent: 'text-signal-amber',
      detail: summary.quietest_operational_backend
        ? `quietest ${summary.quietest_operational_backend}`
        : '—',
    },
    {
      label: 'Jobs in flight',
      value: summary.jobs_in_flight,
      icon: Activity,
      accent: 'text-signal-rose',
      detail: 'queued or running',
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      {tiles.map(({ label, value, icon: Icon, accent, detail }) => (
        <div key={label} className="panel px-4 py-3">
          <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-slate-500">
            <Icon size={12} className={accent} />
            {label}
          </div>
          <div className="mt-1.5 font-mono text-2xl font-semibold tabular-nums text-slate-100">
            {value}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-slate-500" title={detail}>
            {detail}
          </div>
        </div>
      ))}
    </div>
  )
}
