import { Sparkles, ArrowRight } from 'lucide-react'

const RANK_STYLES = [
  'bg-signal-cyan/15 text-signal-cyan ring-signal-cyan/30',
  'bg-signal-violet/15 text-signal-violet ring-signal-violet/30',
  'bg-slate-500/15 text-slate-400 ring-slate-500/30',
]

/**
 * Turns the fleet view into an answer.
 *
 * The grid tells you what the fleet is doing; this tells you what to do about
 * it. Each entry carries its own rationale, because a bare score is not a
 * recommendation -- the user has to be able to disagree with the reasoning.
 */
export default function Recommender({ recommendations }) {
  return (
    <section className="panel">
      <h2 className="panel-heading">
        <Sparkles size={13} className="text-signal-cyan" />
        Where to submit right now
      </h2>

      {recommendations.length === 0 ? (
        <p className="px-4 py-6 text-center text-xs text-slate-500">
          No operational hardware available.
        </p>
      ) : (
        <ol className="divide-y divide-lab-700/50">
          {recommendations.map((rec) => (
            <li key={rec.backend_name} className="flex items-start gap-3 px-4 py-3">
              <span
                className={`mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ring-1 ${
                  RANK_STYLES[rec.rank - 1] ?? RANK_STYLES[2]
                }`}
              >
                {rec.rank}
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate font-mono text-sm text-slate-100">
                    {rec.backend_name}
                  </span>
                  <span className="shrink-0 font-mono text-xs tabular-nums text-slate-500">
                    {rec.score.toFixed(0)}/100
                  </span>
                </div>
                <p className="mt-0.5 text-[11px] leading-relaxed text-slate-400">{rec.rationale}</p>
                <div className="mt-1 flex items-center gap-1 text-[10px] text-slate-600">
                  <ArrowRight size={10} />
                  {rec.queue_length} queued · {rec.qubits} qubits
                </div>
              </div>
            </li>
          ))}
        </ol>
      )}
      <p className="border-t border-lab-700/50 px-4 py-2 text-[10px] leading-relaxed text-slate-600">
        Scored on queue depth (55%), throughput (25%) and qubit count (20%), normalised across the
        operational fleet.
      </p>
    </section>
  )
}
