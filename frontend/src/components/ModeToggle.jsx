import { Radio, FlaskConical } from 'lucide-react'

/**
 * Reflects the backend's actual `source` field.
 *
 * Deliberately *not* an interactive control. The mode is decided by the server
 * -- by whether credentials are present and whether IBM is reachable -- so a
 * clickable frontend switch would be able to lie about where the data came
 * from. It is styled as a segmented control because that reads instantly, and
 * the tooltip says how to actually change modes.
 */
export default function ModeToggle({ source, connectionState }) {
  const isLive = source === 'live'

  return (
    <div
      className="inline-flex items-center rounded-lg border border-lab-700 bg-lab-900/80 p-1"
      title={
        isLive
          ? 'Streaming live telemetry from the IBM Quantum REST API.'
          : 'Simulated telemetry. Add IBM_QUANTUM_API_KEY and IBM_QUANTUM_CRN to .env and restart the backend for live mode.'
      }
    >
      <span
        className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
          isLive ? 'bg-signal-cyan/15 text-signal-cyan' : 'text-slate-500'
        }`}
      >
        <Radio size={13} className={isLive ? 'animate-pulse-dot' : ''} />
        Live IBM QPU
      </span>
      <span
        className={`flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
          !isLive ? 'bg-signal-amber/15 text-signal-amber' : 'text-slate-500'
        }`}
      >
        <FlaskConical size={13} />
        Simulated
      </span>
      <span className="ml-2 mr-1.5 flex items-center gap-1.5 border-l border-lab-700 pl-2.5 text-[11px] text-slate-500">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            connectionState === 'open'
              ? 'animate-pulse-dot bg-signal-green'
              : connectionState === 'reconnecting'
                ? 'bg-signal-amber'
                : 'bg-slate-600'
          }`}
        />
        {connectionState === 'open'
          ? 'streaming'
          : connectionState === 'reconnecting'
            ? 'reconnecting'
            : 'connecting'}
      </span>
    </div>
  )
}
