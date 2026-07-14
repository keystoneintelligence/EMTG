import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import { formatDuration, searchEffortEstimate } from './searchEffort'
import type { SearchEffortPreset, SearchEffortPresetCollection } from './types'

const numericFields: Array<{ key: keyof SearchEffortPreset; label: string; help: string }> = [
  { key: 'parallel_candidates', label: 'Parallel candidates', help: 'Maximum simultaneous single-threaded EMTG processes.' },
  { key: 'population_size', label: 'Population', help: 'Candidates proposed in each NSGA-II generation.' },
  { key: 'generations', label: 'Generations', help: 'Offspring generations after the initial population.' },
  { key: 'stall_generations', label: 'Stall generations', help: 'Stop after this many generations without archive improvement.' },
  { key: 'trials', label: 'Independent trials', help: 'Independent seeded evolutionary searches.' },
  { key: 'solve_time_seconds', label: 'Candidate solve time (s)', help: 'MBH and IPOPT time budget for each candidate.' },
  { key: 'nlp_major_iterations', label: 'IPOPT iterations', help: 'Maximum major iterations for each NLP solve.' },
  { key: 'mbh_max_trials', label: 'MBH trials', help: 'High ceiling; wall time normally controls production runs.' },
  { key: 'watchdog_seconds', label: 'Process watchdog (s)', help: 'Hard process timeout, including startup and output writing.' },
]

export function SearchEffortEditor() {
  const [document, setDocument] = useState<SearchEffortPresetCollection | null>(null)
  const [selectedId, setSelectedId] = useState('')
  const [message, setMessage] = useState('Loading search-effort presets…')

  useEffect(() => {
    api.searchEffortPresets().then(value => {
      setDocument(value)
      setSelectedId(value.default_id)
      setMessage('')
    }).catch(value => setMessage(String(value)))
  }, [])

  const selected = useMemo(
    () => document?.items.find(value => value.id === selectedId) || document?.items[0],
    [document, selectedId],
  )
  if (!document || !selected) return <div className="info-banner">{message}</div>
  const currentDocument = document
  const currentPreset = selected
  const estimate = searchEffortEstimate(currentPreset)

  function replace(preset: SearchEffortPreset) {
    setDocument(previous => previous && ({
      ...previous,
      items: previous.items.map(value => value.id === preset.id ? preset : value),
    }))
  }

  function add() {
    const id = `custom-${Date.now().toString(36)}`
    const preset: SearchEffortPreset = { ...currentPreset, id, name: 'New search effort', description: 'Custom search-effort preset.' }
    setDocument(previous => previous && ({ ...previous, items: [...previous.items, preset] }))
    setSelectedId(id)
    setMessage('New preset added locally; save changes to make it available in Campaign Planner.')
  }

  function remove() {
    if (currentDocument.items.length === 1) return
    const items = currentDocument.items.filter(value => value.id !== currentPreset.id)
    const defaultId = currentDocument.default_id === currentPreset.id ? items[0].id : currentDocument.default_id
    setDocument({ default_id: defaultId, items })
    setSelectedId(defaultId)
    setMessage('Preset removed locally; save changes to apply.')
  }

  async function save() {
    try {
      const saved = await api.saveSearchEffortPresets(currentDocument)
      setDocument(saved)
      setSelectedId(value => saved.items.some(item => item.id === value) ? value : saved.default_id)
      setMessage('Search-effort presets saved. Campaign Planner will use these values for new runs.')
    } catch (value) { setMessage(String(value)) }
  }

  return <section className="search-effort-editor">
    <div className="preset-sidebar">
      <div className="panel-title">Search effort presets</div>
      {currentDocument.items.map(preset => <button className={preset.id === currentPreset.id ? 'active' : ''} key={preset.id} onClick={() => setSelectedId(preset.id)}>
        <strong>{preset.name}</strong>{preset.id === currentDocument.default_id && <small>Default</small>}
      </button>)}
      <button onClick={add}>+ Add preset</button>
    </div>
    <div className="preset-form">
      <div className="preset-toolbar">
        <label>Default preset<select value={currentDocument.default_id} onChange={event => setDocument({ ...currentDocument, default_id: event.target.value })}>{currentDocument.items.map(value => <option key={value.id} value={value.id}>{value.name}</option>)}</select></label>
        <button className="danger" disabled={currentDocument.items.length === 1} onClick={remove}>Remove</button>
        <button className="primary" onClick={save}>Save preset list</button>
      </div>
      {message && <div className="info-banner">{message}</div>}
      <div className="preset-identity">
        <label>Name<input value={currentPreset.name} onChange={event => replace({ ...currentPreset, name: event.target.value })} /></label>
        <label>Description<textarea value={currentPreset.description} onChange={event => replace({ ...currentPreset, description: event.target.value })} /></label>
      </div>
      <div className="preset-grid">{numericFields.map(field => <label key={field.key} title={field.help}>
        <span>{field.label}</span>
        <input type="number" min={field.key === 'generations' ? 0 : 1} value={Number(currentPreset[field.key])} onChange={event => replace({ ...currentPreset, [field.key]: Number(event.target.value) })} />
        <small>{field.help}</small>
      </label>)}</div>
      <div className="effort-estimate">
        <strong>{estimate.proposedCandidates.toLocaleString()} proposed candidates</strong>
        <span>{estimate.activeWorkers} active workers · {estimate.waves} sequential waves · up to {formatDuration(estimate.worstCaseSeconds)}</span>
        {currentPreset.population_size < currentPreset.parallel_candidates && <em>Population is smaller than the worker count; some workers will remain idle.</em>}
        {currentPreset.watchdog_seconds < currentPreset.solve_time_seconds + 30 && <em>Allow at least 30 seconds of watchdog margin for startup and output.</em>}
      </div>
    </div>
  </section>
}
