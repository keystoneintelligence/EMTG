import type { Solution } from './types'

export function ParetoPlot({ solutions, selected, onToggle }: { solutions: Solution[]; selected: Set<string>; onToggle: (solution: Solution) => void }) {
  const values = solutions.filter(value => value.flight_time_days != null && value.delivered_mass_kg != null)
  if (!values.length) return <div className="empty-plot">No comparable objective values.</div>
  const xs = values.map(value => value.flight_time_days!)
  const ys = values.map(value => value.delivered_mass_kg!)
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const x = (value: number) => 45 + (value - minX) / Math.max(1e-9, maxX - minX) * 900
  const y = (value: number) => 170 - (value - minY) / Math.max(1e-9, maxY - minY) * 140
  return <svg className="pareto-plot" viewBox="0 0 1000 200" role="img" aria-label="Flight time versus delivered mass">
    <line x1="45" y1="170" x2="970" y2="170" /><line x1="45" y1="20" x2="45" y2="170" />
    <text x="470" y="195">Flight time (days)</text><text x="12" y="110" transform="rotate(-90 12 110)">Delivered mass (kg)</text>
    {values.map(value => <circle key={value.id} cx={x(value.flight_time_days!)} cy={y(value.delivered_mass_kg!)} r={selected.has(value.id) ? 6 : value.pareto ? 4.5 : 3} className={selected.has(value.id) ? 'selected-point' : value.pareto ? 'pareto-point' : ''} onClick={() => onToggle(value)} />)}
  </svg>
}
