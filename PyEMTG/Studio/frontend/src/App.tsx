import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, token } from './api'
import { MissionEditor } from './MissionEditor'
import { ParetoPlot } from './ParetoPlot'
import { QueuePanel } from './QueuePanel'
import { SolutionTable } from './SolutionTable'
import { TrajectoryScene } from './TrajectoryScene'
import { utcDateFromTdbMjd } from './missionTime'
import type { BodyOption, BodyTrajectory, Job, Solution, Trajectory } from './types'

type View = 'mission' | 'queue' | 'editor'

export function App() {
  const [view, setView] = useState<View>('mission')
  const [jobs, setJobs] = useState<Job[]>([])
  const [solutions, setSolutions] = useState<Solution[]>([])
  const [total, setTotal] = useState(0)
  const [globalCores, setGlobalCores] = useState(1)
  const [selectedIds, setSelectedIds] = useState(new Set<string>())
  const [trajectories, setTrajectories] = useState(new Map<string, Trajectory>())
  const [bodyOptions, setBodyOptions] = useState<BodyOption[]>([])
  const [sceneBodies, setSceneBodies] = useState(new Set<string>())
  const [bodyTrajectories, setBodyTrajectories] = useState<BodyTrajectory[]>([])
  const [startBody, setStartBody] = useState('')
  const [endBody, setEndBody] = useState('')
  const [sequence, setSequence] = useState('')
  const [propellantMax, setPropellantMax] = useState('')
  const [feasibleOnly, setFeasibleOnly] = useState(true)
  const [epoch, setEpoch] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [timeMode, setTimeMode] = useState<'utc' | 'mjd' | 'elapsed'>('utc')
  const [catalogMode, setCatalogMode] = useState<'table' | 'pareto'>('table')
  const [error, setError] = useState('')
  const jobSignature = useRef('')

  const refreshJobs = useCallback(() => api.jobs().then(value => { setJobs(value.items); setGlobalCores(value.global_core_limit) }).catch(value => setError(String(value))), [])
  const refreshSolutions = useCallback(() => {
    const query = new URLSearchParams({ limit: '500' })
    if (startBody) query.set('start_body', startBody)
    if (endBody) query.set('end_body', endBody)
    if (sequence) query.set('sequence', sequence)
    if (propellantMax) query.set('propellant_max', propellantMax)
    if (feasibleOnly) query.set('feasible', 'true'); else query.delete('feasible')
    api.solutions(query).then(value => { setSolutions(value.items); setTotal(value.total) }).catch(value => setError(String(value)))
  }, [startBody, endBody, sequence, propellantMax, feasibleOnly])

  useEffect(() => { refreshJobs(); refreshSolutions() }, [refreshJobs, refreshSolutions])
  useEffect(() => {
    api.bodies().then(value => {
      setBodyOptions(value.items)
      setSceneBodies(previous => {
        if (previous.size) return previous
        return new Set(value.items.filter(body => ['Earth', 'A20136163'].includes(body.name)).map(body => body.name))
      })
    }).catch(value => setError(String(value)))
  }, [])
  useEffect(() => {
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/api/v1/events?access_token=${encodeURIComponent(token)}`)
    socket.onmessage = event => {
      const value = JSON.parse(event.data)
      if (value.type === 'snapshot') {
        setJobs(value.jobs); setGlobalCores(value.global_core_limit)
        const signature = JSON.stringify(value.jobs.map((job: Job) => [job.id, job.status, job.updated_at]))
        if (signature !== jobSignature.current) { jobSignature.current = signature; refreshSolutions() }
      }
    }
    return () => socket.close()
  }, [refreshSolutions])

  const selected = useMemo(() => solutions.filter(solution => selectedIds.has(solution.id)), [solutions, selectedIds])
  const selectedTrajectories = useMemo(
    () => {
      const visibleSelectedIds = new Set(selected.map(solution => solution.id))
      return new Map([...trajectories].filter(([id]) => visibleSelectedIds.has(id)))
    },
    [trajectories, selected],
  )
  const selectedEndpointSignature = selected.map(solution => `${solution.start_body || ''}:${solution.end_body || ''}`).sort().join('|')
  useEffect(() => {
    if (!selected.length || !bodyOptions.length) return
    const available = new Set(bodyOptions.map(body => body.name))
    const endpoints = selected
      .flatMap(solution => [solution.start_body, solution.end_body])
      .filter((value): value is string => Boolean(value) && available.has(value as string))
    setSceneBodies(previous => new Set([...previous, ...endpoints]))
  }, [selectedEndpointSignature, bodyOptions.length])
  const allSamples = [...selectedTrajectories.values()].flatMap(value => value.samples)
  const timeAnchors = allSamples.filter(sample => sample.tdb_minus_utc_seconds != null)
  const minEpoch = allSamples.length ? Math.min(...allSamples.map(value => value.epoch_mjd)) : 0
  const maxEpoch = allSamples.length ? Math.max(...allSamples.map(value => value.epoch_mjd)) : 1
  const sceneBodyKey = [...sceneBodies].sort().join('|')
  useEffect(() => {
    const names = [...sceneBodies].filter(name => bodyOptions.some(body => body.name === name))
    if (!allSamples.length || !names.length || maxEpoch <= minEpoch) {
      setBodyTrajectories([])
      return
    }
    let active = true
    api.bodyEphemerides(names, minEpoch, maxEpoch, 420)
      .then(value => { if (active) setBodyTrajectories(value.series) })
      .catch(value => { if (active) setError(String(value)) })
    return () => { active = false }
  }, [sceneBodyKey, minEpoch, maxEpoch, allSamples.length, bodyOptions.length])
  useEffect(() => { if (allSamples.length && (epoch < minEpoch || epoch > maxEpoch)) setEpoch(minEpoch) }, [minEpoch, maxEpoch, allSamples.length, epoch])
  useEffect(() => {
    if (!playing || maxEpoch <= minEpoch) return
    const timer = window.setInterval(() => setEpoch(value => value >= maxEpoch ? minEpoch : Math.min(maxEpoch, value + (maxEpoch - minEpoch) / 500 * speed)), 30)
    return () => window.clearInterval(timer)
  }, [playing, speed, minEpoch, maxEpoch])

  async function toggle(solution: Solution) {
    const next = new Set(selectedIds)
    if (next.has(solution.id)) next.delete(solution.id)
    else if (next.size < 10) next.add(solution.id)
    setSelectedIds(next)
    if (next.has(solution.id) && !trajectories.has(solution.id)) {
      try { const trajectory = await api.trajectory(solution.id); setTrajectories(previous => new Map(previous).set(solution.id, trajectory)) }
      catch (value) { setError(String(value)) }
    }
  }

  function toggleBody(name: string) {
    setSceneBodies(previous => {
      const next = new Set(previous)
      if (next.has(name)) next.delete(name); else next.add(name)
      return next
    })
  }

  const inspected = selected[selected.length - 1]
  const formatEpoch = (value: number) => {
    if (timeMode === 'mjd') return `TDB MJD ${value.toFixed(5)}`
    if (timeMode === 'elapsed') return `T + ${(value - minEpoch).toFixed(3)} days`
    const utc = utcDateFromTdbMjd(value, timeAnchors)
    return utc ? utc.toISOString().replace('.000Z', 'Z') : `TDB MJD ${value.toFixed(5)} (UTC unavailable)`
  }
  return <div className="app-shell">
    <header>
      <div className="brand"><span className="orbit-mark">◉</span><div><strong>EMTG Studio</strong><small>Mission Design Workbench</small></div></div>
      <nav><button className={view === 'mission' ? 'active' : ''} onClick={() => setView('mission')}>Mission View</button><button className={view === 'queue' ? 'active' : ''} onClick={() => setView('queue')}>Campaign Planner</button><button className={view === 'editor' ? 'active' : ''} onClick={() => setView('editor')}>Mission Configuration</button></nav>
      <div className="system-state"><span className={jobs.some(job => job.status === 'running') ? 'running-dot' : 'idle-dot'} />{jobs.some(job => job.status === 'running') ? 'Solving' : 'Ready'} · {globalCores} cores</div>
    </header>
    {error && <div className="error-banner global-error" onClick={() => setError('')}>{error}</div>}

    {view === 'queue' && <QueuePanel jobs={jobs} globalCores={globalCores} refresh={refreshJobs} />}
    {view === 'editor' && <MissionEditor />}
    {view === 'mission' && <>
      <div className="workbench">
        <aside className="scene-tree">
          <div className="panel-title">Solution layers <span>{selected.length}/10</span></div>
          <div className="tree-node expanded">▾ Reference frame<div className="tree-child">◉ J2000 / ICRF</div></div>
          <div className="tree-node expanded">▾ Trajectories
            {selected.map((solution, index) => <button className="layer-row" key={solution.id} onClick={() => toggle(solution)}><span style={{ color: ['#53d8fb','#ffb454','#a78bfa','#63e6be'][index % 4] }}>━</span>{solution.sequence_text}</button>)}
            {!selected.length && <small>Select solutions from the table below.</small>}
          </div>
          <div className="tree-node expanded body-track-tree">▾ SPICE body tracks <small>{bodyTrajectories.filter(body => body.samples.length).length} plotted · cumulative mission span</small>
            {bodyOptions.map(body => {
              const series = bodyTrajectories.find(value => value.name === body.name)
              const status = series && series.coverage_status !== 'covered' ? series.coverage_status : body.category
              return <label key={body.name} title={`SPICE ${body.spice_id} · ${body.kernel_files.join(', ')}`}><input type="checkbox" checked={sceneBodies.has(body.name)} onChange={() => toggleBody(body.name)} />{body.display_name}<span className={`coverage-${status}`}>{status}</span></label>
            })}
          </div>
          <div className="filter-block"><div className="panel-title">Catalog filters</div><label>Start body<input value={startBody} placeholder="Earth" onChange={event => setStartBody(event.target.value)} /></label><label>End body<input value={endBody} placeholder="Mars" onChange={event => setEndBody(event.target.value)} /></label><label>Route contains<input value={sequence} placeholder="Venus" onChange={event => setSequence(event.target.value)} /></label><label>Max propellant (kg)<input type="number" value={propellantMax} onChange={event => setPropellantMax(event.target.value)} /></label><label className="check"><input type="checkbox" checked={feasibleOnly} onChange={event => setFeasibleOnly(event.target.checked)} /> Feasible only</label><button onClick={refreshSolutions}>Apply filters</button></div>
        </aside>
        <main className="viewport"><TrajectoryScene trajectories={selectedTrajectories} selected={selected} bodyTrajectories={bodyTrajectories} epoch={epoch} /><div className="viewport-badge">J2000 / ICRF · velocity-smoothed trajectories · Earth-plane grid</div></main>
        <aside className="inspector">
          <div className="panel-title">Inspector</div>
          {inspected ? <><h3>{inspected.sequence_text}</h3><dl><dt>Status</dt><dd>{inspected.status}</dd><dt>Fidelity</dt><dd>{inspected.fidelity || '—'}</dd><dt>Launch</dt><dd>{inspected.launch_mjd?.toFixed(3) || '—'} MJD</dd><dt>Flight time</dt><dd>{inspected.flight_time_days?.toFixed(2) || '—'} d</dd><dt>Propellant</dt><dd>{inspected.propellant_used_kg?.toFixed(2) || '—'} kg</dd><dt>Delivered mass</dt><dd>{inspected.delivered_mass_kg?.toFixed(2) || '—'} kg</dd><dt>Max thrust</dt><dd>{inspected.thrust_max_n?.toPrecision(4) || '—'} N</dd></dl><button onClick={() => api.materialize(inspected.id).then(refreshSolutions)}>Request dense ephemeris</button></> : <p>Select a solution to inspect its metrics and trajectory.</p>}
        </aside>
      </div>
      <section className="timeline"><div className="timeline-controls"><button onClick={() => setPlaying(!playing)}>{playing ? '❚❚' : '▶'}</button><button onClick={() => setEpoch(minEpoch)}>↤</button><strong>{epoch ? formatEpoch(epoch) : 'No trajectory loaded'}</strong><label>Time <select value={timeMode} onChange={event => setTimeMode(event.target.value as typeof timeMode)}><option value="utc">UTC</option><option value="mjd">MJD</option><option value="elapsed">Elapsed</option></select></label><label>Speed <select value={speed} onChange={event => setSpeed(Number(event.target.value))}><option value={0.25}>0.25×</option><option value={1}>1×</option><option value={4}>4×</option><option value={16}>16×</option></select></label></div><input type="range" min={minEpoch} max={maxEpoch} step={(maxEpoch - minEpoch) / 10000 || 1} value={epoch} onChange={event => setEpoch(Number(event.target.value))} /><div className="time-labels"><span>{minEpoch ? minEpoch.toFixed(2) : '—'}</span><span>Full trajectory remains visible while time advances</span><span>{maxEpoch ? maxEpoch.toFixed(2) : '—'}</span></div></section>
      <section className="catalog"><div className="panel-title"><span>Solution catalog <button className={catalogMode === 'table' ? 'active' : ''} onClick={() => setCatalogMode('table')}>Table</button><button className={catalogMode === 'pareto' ? 'active' : ''} onClick={() => setCatalogMode('pareto')}>Pareto</button></span><span>{total.toLocaleString()} evaluations · {selected.length} compared</span></div>{catalogMode === 'table' ? <SolutionTable solutions={solutions} selected={selectedIds} onToggle={toggle} /> : <ParetoPlot solutions={solutions} selected={selectedIds} onToggle={toggle} />}</section>
    </>}
  </div>
}
