import { Cpu, Users, Timer, Zap } from 'lucide-react'

const STATUS_STYLES = {
  online: { dot: 'bg-signal-green', ring: 'border-lab-700/70', label: 'text-signal-green', pulse: true },
  paused: { dot: 'bg-signal-amber', ring: 'border-signal-amber/30', label: 'text-signal-amber', pulse: false },
  offline: { dot: 'bg-signal-rose', ring: 'border-signal-rose/30', label: 'text-signal-rose', pulse: false },
  unknown: { dot: 'bg-slate-500', ring: 'border-lab-700/70', label: 'text-slate-500', pulse: false },
}

/** Human-readable wait. Precision past the minute is noise at this scale. */
function formatWait(seconds) {
  if (!seconds) return '—'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  return `${hours}h ${minutes % 60}m`
}

/** Colour the queue number by how bad the wait actually is. */
function queueTone(queueLength) {
  if (queueLength >= 200) return 'text-signal-rose'
  if (queueLength >= 75) return 'text-signal-amber'
  return 'text-signal-green'
}

export default function QPUCard({ backend, isRecommended }) {
  const style = STATUS_STYLES[backend.status] ?? STATUS_STYLES.unknown

  return (
    <div
      className={`panel relative px-4 py-3 transition-colors hover:border-lab-600 ${style.ring} ${
        isRecommended ? 'ring-1 ring-signal-cyan/40' : ''
      }`}
    >
      {isRecommended && (
        <span className="absolute -top-2 right-3 rounded-full bg-signal-cyan/15 px-2 py-0.5 text-[10px] font-medium text-signal-cyan ring-1 ring-signal-cyan/30">
          recommended
        </span>
      )}

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={`h-2 w-2 shrink-0 rounded-full ${style.dot} ${
                style.pulse ? 'animate-pulse-dot' : ''
              }`}
            />
            <span className="truncate font-mono text-sm font-medium text-slate-100">
              {backend.name}
            </span>
          </div>
          <div className="mt-0.5 pl-4 text-[11px] text-slate-500">
            {backend.processor_type ?? 'Unknown processor'}
          </div>
        </div>
        <span className={`shrink-0 text-[11px] font-medium uppercase ${style.label}`}>
          {backend.status}
        </span>
      </div>

      {backend.status_reason && (
        <div className="mt-2 rounded-md bg-lab-850 px-2 py-1 text-[11px] text-slate-400">
          {backend.status_reason}
        </div>
      )}

      <div className="mt-3 grid grid-cols-3 gap-2 border-t border-lab-700/60 pt-2.5">
        <Metric
          icon={Users}
          label="queue"
          value={backend.queue_length}
          tone={backend.is_operational ? queueTone(backend.queue_length) : 'text-slate-600'}
        />
        <Metric icon={Cpu} label="qubits" value={backend.qubits} tone="text-slate-200" />
        <Metric
          icon={Timer}
          label="est. wait"
          value={backend.is_operational ? formatWait(backend.estimated_wait_seconds) : '—'}
          tone="text-slate-200"
          title="Estimated from queue depth and processor speed. IBM does not publish a wait time."
        />
      </div>

      {backend.clops != null && (
        <div className="mt-2 flex items-center gap-1 text-[10px] text-slate-600">
          <Zap size={10} />
          {backend.clops.toLocaleString()} CLOPS
        </div>
      )}
    </div>
  )
}

function Metric({ icon: Icon, label, value, tone, title }) {
  return (
    <div title={title}>
      <div className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-slate-600">
        <Icon size={10} />
        {label}
      </div>
      <div className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${tone}`}>{value}</div>
    </div>
  )
}
