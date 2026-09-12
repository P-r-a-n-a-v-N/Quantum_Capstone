import { useMemo } from 'react'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { TrendingUp } from 'lucide-react'

// Categorical palette. Each series is also given a distinct dash pattern below,
// so the chart stays readable for viewers who cannot separate these by hue.
const SERIES = [
  { colour: '#22d3ee', dash: '0' },
  { colour: '#a78bfa', dash: '6 3' },
  { colour: '#34d399', dash: '2 3' },
  { colour: '#fbbf24', dash: '8 3 2 3' },
  { colour: '#fb7185', dash: '4 4' },
]

const formatClock = (ms) =>
  new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

export default function QueueChart({ chartData, backends }) {
  // Plot the five busiest operational machines. Eighteen lines is not a chart,
  // it is a plate of spaghetti.
  const tracked = useMemo(
    () =>
      backends
        .filter((b) => !b.is_simulator && b.is_operational)
        .sort((a, b) => b.queue_length - a.queue_length)
        .slice(0, 5)
        .map((b) => b.name),
    [backends],
  )

  const hasData = chartData.length >= 2

  return (
    <section className="panel flex h-full flex-col">
      <h2 className="panel-heading">
        <TrendingUp size={13} className="text-signal-violet" />
        Queue depth · five busiest QPUs
        <span className="ml-auto normal-case tracking-normal text-slate-600">
          {chartData.length} samples
        </span>
      </h2>

      <div className="min-h-[260px] flex-1 px-2 py-3">
        {!hasData ? (
          <div className="flex h-full min-h-[240px] items-center justify-center text-xs text-slate-600">
            Collecting samples…
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%" minHeight={240}>
            <LineChart data={chartData} margin={{ top: 4, right: 12, bottom: 0, left: -18 }}>
              <CartesianGrid strokeDasharray="2 4" stroke="#1e293f" vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={['dataMin', 'dataMax']}
                tickFormatter={formatClock}
                stroke="#475569"
                tick={{ fontSize: 10 }}
                tickLine={false}
                axisLine={{ stroke: '#1e293f' }}
                minTickGap={44}
              />
              <YAxis
                stroke="#475569"
                tick={{ fontSize: 10 }}
                tickLine={false}
                axisLine={false}
                width={46}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={{
                  background: '#0a0e1a',
                  border: '1px solid #1e293f',
                  borderRadius: 8,
                  fontSize: 12,
                }}
                labelFormatter={(value) => new Date(value).toLocaleTimeString()}
                itemStyle={{ padding: '1px 0' }}
              />
              <Legend
                wrapperStyle={{ fontSize: 10, paddingTop: 6 }}
                iconType="plainline"
                iconSize={14}
              />
              {tracked.map((name, index) => {
                const { colour, dash } = SERIES[index % SERIES.length]
                return (
                  <Line
                    key={name}
                    type="monotone"
                    dataKey={name}
                    stroke={colour}
                    strokeWidth={1.6}
                    strokeDasharray={dash}
                    dot={false}
                    // Values are missing for ticks predating a backend appearing;
                    // bridge them rather than breaking the line into fragments.
                    connectNulls
                    isAnimationActive={false}
                  />
                )
              })}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </section>
  )
}
