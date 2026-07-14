import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import { applySearchEffort, formatDuration, searchEffortEstimate } from './searchEffort'
import type { BodyOption, Job, SearchEffortPreset } from './types'

function BodySelector({ id, label, value, excluded, bodies, onChange }: {
  id: string; label: string; value: string; excluded: string;
  bodies: BodyOption[]; onChange: (value: string) => void
}) {
  const available = bodies.filter(body => body.name !== excluded)
  return <label className="body-selector">{label}
    <input list={`${id}-options`} value={value} onChange={event => onChange(event.target.value)} autoComplete="off" placeholder="Type to filter bodies" />
    <datalist id={`${id}-options`}>
      {available.map(body => <option key={`${body.spice_id}-${body.name}`} value={body.name}>{body.display_name} · SPICE {body.spice_id} · {body.category}</option>)}
    </datalist>
    <small>{available.length} kernel-backed choices</small>
  </label>
}

function evaluatorType(job: Job): string {
  const evaluator = job.config?.evaluator
  return evaluator && typeof evaluator === 'object' && 'type' in evaluator
    ? String((evaluator as Record<string, unknown>).type)
    : 'unknown'
}

function syntheticConfiguration(source: Record<string, unknown>): Record<string, unknown> {
  const value = structuredClone(source)
  delete value.base_case
  delete value.assets
  delete value.fidelities
  delete value.seeds
  value.evaluator = { type: 'synthetic', problem: 'architecture' }
  value.algorithm = {
    population_size: 12, generations: 8, tournament_size: 2,
    crossover_probability: 0.9, mutation_probability: 0.7,
    stall_generations: 5, trials: 1,
  }
  return value
}

export function QueuePanel({ jobs, globalCores, refresh }: { jobs: Job[]; globalCores: number; refresh: () => void }) {
  const [name, setName] = useState('Earth to A20136163 search')
  const [config, setConfig] = useState('Loading real EMTG configuration…')
  const [startBody, setStartBody] = useState('Earth')
  const [endBody, setEndBody] = useState('A20136163')
  const [syntheticDebug, setSyntheticDebug] = useState(false)
  const [bodies, setBodies] = useState<BodyOption[]>([])
  const [bodyDiscovery, setBodyDiscovery] = useState('Discovering SPICE bodies…')
  const [defaultsReady, setDefaultsReady] = useState(false)
  const [defaultsMessage, setDefaultsMessage] = useState('Discovering EMTG runtime assets…')
  const [error, setError] = useState('')
  const [searchEfforts, setSearchEfforts] = useState<SearchEffortPreset[]>([])
  const [searchEffortId, setSearchEffortId] = useState('')
  const realConfiguration = useRef('')
  const selectedEffort = searchEfforts.find(value => value.id === searchEffortId) || searchEfforts[0]
  const effortEstimate = selectedEffort ? searchEffortEstimate(selectedEffort) : null

  useEffect(() => {
    Promise.all([api.searchDefaults(), api.searchEffortPresets()]).then(([value, efforts]) => {
      const preset = efforts.items.find(item => item.id === efforts.default_id) || efforts.items[0]
      const configured = applySearchEffort(value.config, preset)
      const serialized = JSON.stringify(configured, null, 2)
      realConfiguration.current = serialized
      setConfig(serialized)
      setSearchEfforts(efforts.items)
      setSearchEffortId(preset.id)
      setDefaultsReady(value.ready)
      setDefaultsMessage(value.ready
        ? `Real EMTG ready · ${value.template} · ${value.runtime_root}`
        : `Real EMTG is not ready: ${value.missing.join('; ')}`)
      const search = value.config.search as Record<string, unknown> | undefined
      if (search?.fixed_start) setStartBody(String(search.fixed_start))
      if (search?.fixed_final) setEndBody(String(search.fixed_final))
      setName(`${value.template} search`)
    }).catch(value => { setError(String(value)); setDefaultsMessage('Unable to discover EMTG runtime assets or search-effort presets') })
  }, [])

  useEffect(() => {
    api.bodies().then(value => {
      setBodies(value.items)
      setBodyDiscovery(value.ready
        ? `${value.count} selectable bodies from ${value.kernel_files.length} SPICE kernels · ${value.central_body}`
        : `Body discovery unavailable: ${value.error || 'no kernel-backed bodies in the active universe'}`)
    }).catch(value => setBodyDiscovery(`Body discovery failed: ${String(value)}`))
  }, [])

  async function create() {
    try {
      setError('')
      if (!syntheticDebug && !defaultsReady) throw new Error(defaultsMessage)
      if (!selectedEffort) throw new Error('Select a search-effort preset')
      const source = JSON.parse(config)
      const submitted = syntheticDebug ? source : applySearchEffort(source, selectedEffort)
      if (!syntheticDebug) {
        const serialized = JSON.stringify(submitted, null, 2)
        setConfig(serialized)
        realConfiguration.current = serialized
      }
      await api.createJob({ name, requested_cores: selectedEffort.parallel_candidates, queue: true, config: submitted })
      refresh()
    } catch (value) { setError(String(value)) }
  }

  function selectSearchEffort(identifier: string) {
    try {
      const preset = searchEfforts.find(value => value.id === identifier)
      if (!preset) throw new Error('Search-effort preset is unavailable')
      const source = JSON.parse(syntheticDebug ? realConfiguration.current : config)
      const serialized = JSON.stringify(applySearchEffort(source, preset), null, 2)
      realConfiguration.current = serialized
      if (!syntheticDebug) setConfig(serialized)
      setSearchEffortId(identifier)
      setError('')
    } catch (value) { setError(String(value)) }
  }

  function applyRoute() {
    try {
      if (startBody === endBody) throw new Error('Start and end bodies must be different')
      if (bodies.length && !bodies.some(body => body.name === startBody)) throw new Error(`Select a discovered start body; ${startBody} is not available`)
      if (bodies.length && !bodies.some(body => body.name === endBody)) throw new Error(`Select a discovered end body; ${endBody} is not available`)
      const value = JSON.parse(config)
      value.search = { ...(value.search || {}), fixed_start: startBody, fixed_final: endBody }
      const serialized = JSON.stringify(value, null, 2)
      setConfig(serialized)
      if (!syntheticDebug) realConfiguration.current = serialized
      setError('')
    } catch (value) { setError(String(value)) }
  }

  function setDebugMode(enabled: boolean) {
    try {
      if (enabled) {
        realConfiguration.current = config
        setConfig(JSON.stringify(syntheticConfiguration(JSON.parse(config)), null, 2))
      } else {
        setConfig(realConfiguration.current)
      }
      setSyntheticDebug(enabled)
      setError('')
    } catch (value) { setError(String(value)) }
  }

  async function remove(job: Job) {
    const count = String(job.progress?.evaluation_count ?? 'all')
    if (!window.confirm(`Delete "${job.name}" and ${count} associated catalog evaluations and run artifacts? This cannot be undone.`)) return
    try {
      const result = await api.deleteJob(job.id)
      setError('')
      refresh()
      window.alert(`Deleted ${result.deleted_solutions} solution records from ${job.name}.`)
    } catch (value) { setError(String(value)) }
  }

  return <div className="queue-panel">
    <section className="queue-list">
      <div className="panel-title"><span>Search queue</span><label>Global cores <input type="number" min={1} value={globalCores} onChange={event => api.setGlobalCores(Number(event.target.value)).then(refresh)} /></label></div>
      {jobs.map(job => {
        const evaluator = evaluatorType(job)
        return <article className={`job job-${job.status}`} key={job.id}>
          <div className="job-heading"><strong>{job.name}</strong><span className="job-badges"><span className="status-chip">{job.status}</span>{evaluator === 'synthetic' && <span className="synthetic-chip">synthetic</span>}{evaluator === 'emtg' && <span className="emtg-chip">real EMTG</span>}</span></div>
          <small>{job.effective_cores}/{job.requested_cores} parallel candidates · {String(job.progress.checkpoint_status || 'not started')}</small>
          <div className="job-actions">
            {job.status === 'running' && <button onClick={() => api.jobAction(job.id, 'pause').then(refresh)}>Pause</button>}
            {job.status === 'paused' && <button onClick={() => api.jobAction(job.id, 'resume').then(refresh)}>Resume</button>}
            {['queued', 'paused', 'running'].includes(job.status) && <button className="danger" onClick={() => api.jobAction(job.id, 'cancel').then(refresh)}>Cancel</button>}
            {!['running', 'pausing'].includes(job.status) && <button className="danger" onClick={() => remove(job)}>Delete run</button>}
            <input title="Requested parallel candidates" type="number" min={1} value={job.requested_cores} onChange={event => api.setJobCores(job.id, Number(event.target.value)).then(refresh)} />
          </div>
          <details className="job-config"><summary>View immutable run JSON</summary><pre>{JSON.stringify(job.config, null, 2)}</pre></details>
          {job.error && <pre className="job-error">{job.error}</pre>}
        </article>
      })}
    </section>
    <section className="new-search">
      <div className="panel-title">New outer-loop search</div>
      <div className={defaultsReady ? 'runtime-banner ready' : 'runtime-banner blocked'}>{defaultsMessage}</div>
      <label className="debug-toggle"><input type="checkbox" checked={syntheticDebug} onChange={event => setDebugMode(event.target.checked)} /><span><strong>Use synthetic debug evaluator</strong><small>Qualification only. Produces nonphysical test trajectories and metrics.</small></span></label>
      <div className="form-row"><label>Name<input value={name} onChange={event => setName(event.target.value)} /></label><label>Search effort<select value={searchEffortId} onChange={event => selectSearchEffort(event.target.value)}>{searchEfforts.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</select></label></div>
      {selectedEffort && effortEstimate && <div className="runner-effort-summary">
        <strong>{selectedEffort.name}</strong><span>{selectedEffort.description}</span>
        <small>{selectedEffort.parallel_candidates} requested workers ({Math.min(selectedEffort.parallel_candidates, globalCores)} available) · population {selectedEffort.population_size} · {selectedEffort.generations} generations · {selectedEffort.solve_time_seconds}s/candidate</small>
        <small>Up to {effortEstimate.proposedCandidates.toLocaleString()} proposed candidates · {effortEstimate.waves} waves · {formatDuration(effortEstimate.worstCaseSeconds)} worst-case wall time</small>
        {selectedEffort.population_size < selectedEffort.parallel_candidates && <em>Population is smaller than the worker count; some workers will remain idle.</em>}
      </div>}
      <div className="body-discovery">{bodyDiscovery}</div>
      <div className="form-row route-wizard"><BodySelector id="start-body" label="Start body" value={startBody} excluded={endBody} bodies={bodies} onChange={setStartBody} /><BodySelector id="end-body" label="End body" value={endBody} excluded={startBody} bodies={bodies} onChange={setEndBody} /><button onClick={applyRoute}>Apply route</button></div>
      <textarea value={config} onChange={event => { setConfig(event.target.value); if (!syntheticDebug) realConfiguration.current = event.target.value }} spellCheck={false} />
      {error && <div className="error-banner">{error}</div>}
      <button className="primary" disabled={(!syntheticDebug && !defaultsReady) || !selectedEffort} onClick={create}>{syntheticDebug ? 'Validate and queue synthetic debug search' : 'Validate and queue real EMTG search'}</button>
    </section>
  </div>
}
