import { ListOrdered } from 'lucide-react'

const STATUS_PILL = {
  QUEUED: 'bg-signal-violet/15 text-signal-violet ring-signal-violet/25',
  RUNNING: 'bg-signal-cyan/15 text-signal-cyan ring-signal-cyan/25',
  COMPLETED: 'bg-signal-green/15 text-signal-green ring-signal-green/25',
  FAILED: 'bg-signal-rose/15 text-signal-rose ring-signal-rose/25',
  CANCELLED: 'bg-slate-500/15 text-slate-400 ring-slate-500/25',
  UNKNOWN: 'bg-slate-500/15 text-slate-400 ring-slate-500/25',
}

/** Relative time, because "3 min ago" is what an operator actually reads. */
function timeAgo(iso) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  return hours < 24 ? `${hours}h ago` : `${Math.floor(hours / 24)}d ago`
}

export default function JobStream({ jobs }) {
  return (
    <section className="panel flex h-full flex-col">
      <h2 className="panel-heading">
        <ListOrdered size={13} className="text-signal-green" />
        Live job stream
        <span className="ml-auto normal-case tracking-normal text-slate-600">
          {jobs.length} recent
        </span>
      </h2>

      <div className="max-h-[420px] flex-1 divide-y divide-lab-700/40 overflow-y-auto">
        {jobs.length === 0 ? (
          <p className="px-4 py-8 text-center text-xs text-slate-600">No recent jobs.</p>
        ) : (
          jobs.map((job) => (
            <div key={job.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-lab-850/50">
              <span
                className={`w-[86px] shrink-0 rounded-full px-2 py-0.5 text-center text-[10px] font-medium uppercase ring-1 ${
                  STATUS_PILL[job.status] ?? STATUS_PILL.UNKNOWN
                }`}
              >
                {job.status}
              </span>
              <div className="min-w-0 flex-1">
                <div className="truncate font-mono text-[11px] text-slate-300">{job.id}</div>
                <div className="mt-0.5 flex items-center gap-1.5 text-[10px] text-slate-500">
                  <span className="truncate">{job.backend}</span>
                  {job.program_id && (
                    <>
                      <span className="text-slate-700">·</span>
                      <span className="truncate">{job.program_id}</span>
                    </>
                  )}
                </div>
              </div>
              <div className="shrink-0 text-right">
                <div className="font-mono text-[10px] tabular-nums text-slate-500">
                  {timeAgo(job.created)}
                </div>
                {job.qpu_charge_time_seconds != null && (
                  <div
                    className="font-mono text-[10px] tabular-nums text-slate-600"
                    title="QPU charge time"
                  >
                    {job.qpu_charge_time_seconds.toFixed(1)}s
                  </div>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  )
}
