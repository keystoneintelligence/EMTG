import { useEffect, useMemo, useState } from 'react'
import { api } from './api'
import { SearchEffortEditor } from './SearchEffortEditor'
import type { OptionField } from './types'

const SEARCH_EFFORT_GROUP = 'Search effort presets'

export function MissionEditor() {
  const [schema, setSchema] = useState<OptionField[]>([])
  const [path, setPath] = useState('PyEMTG/default.emtgopt')
  const [mission, setMission] = useState<Record<string, unknown>>({})
  const [journeys, setJourneys] = useState<Record<string, unknown>[]>([])
  const [group, setGroup] = useState(SEARCH_EFFORT_GROUP)
  const [search, setSearch] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => { api.optionSchema().then(value => setSchema(value.items)).catch(value => setMessage(String(value))) }, [])
  const groups = useMemo(() => [SEARCH_EFFORT_GROUP, ...new Set(schema.map(field => field.group))], [schema])
  const defaultSolver = schema.find(field => field.name === 'NLP_solver_type')?.default
  const selectedSolver = Number(mission.NLP_solver_type ?? defaultSolver ?? 2)
  const normalizedSearch = search.toLowerCase()
  const visible = schema.filter(field =>
    field.scope === 'mission'
    && field.group === group
    && (!field.applicable_solvers.length || field.applicable_solvers.includes(selectedSolver))
    && [field.name, ...field.aliases].some(name => name.toLowerCase().includes(normalizedSearch))
  )

  async function open() {
    try { const value = await api.openOptions(path); setMission(value.mission); setJourneys(value.journeys); setPath(value.path); setMessage(`Opened ${value.path}`) }
    catch (value) { setMessage(String(value)) }
  }
  async function save() {
    try { const value = await api.saveOptions({ path, mission, journeys }); setMessage(`Saved ${value.saved}`) }
    catch (value) { setMessage(String(value)) }
  }
  function change(field: OptionField, raw: string | boolean) {
    let value: unknown = raw
    if (field.data_type === 'bool') value = raw ? 1 : 0
    else if (field.data_type.includes('vector')) { try { value = JSON.parse(String(raw)) } catch { value = raw } }
    else if (!field.data_type.includes('string')) { const number = Number(raw); value = Number.isNaN(number) ? raw : number }
    setMission(previous => ({ ...previous, [field.name]: value }))
  }
  return <div className="mission-editor">
    <aside><div className="panel-title">Configuration sections</div>{groups.map(value => <button className={value === group ? 'active' : ''} key={value} onClick={() => setGroup(value)}>{value}</button>)}</aside>
    <main>
      {group === SEARCH_EFFORT_GROUP ? <SearchEffortEditor /> : <>
        <div className="editor-toolbar"><input className="path-input" value={path} onChange={event => setPath(event.target.value)} /><button onClick={open}>Open</button><button className="primary" onClick={save}>Save</button><input placeholder="Filter fields" value={search} onChange={event => setSearch(event.target.value)} /></div>
        {message && <div className="info-banner">{message}</div>}
        <div className="option-grid">{visible.map(field => <label key={field.name} title={field.description}>
          <span>{field.name}</span>
          {field.data_type === 'bool'
            ? <input type="checkbox" checked={Boolean(mission[field.name])} onChange={event => change(field, event.target.checked)} />
            : field.choices.length
              ? <select value={String(mission[field.name] ?? field.default ?? '')} onChange={event => change(field, event.target.value)}>{field.choices.map(choice => <option key={choice.value} value={choice.value}>{choice.label}</option>)}</select>
              : <input value={typeof mission[field.name] === 'object' ? JSON.stringify(mission[field.name]) : String(mission[field.name] ?? field.default ?? '')} onChange={event => change(field, event.target.value)} />}
          <small>{field.description.split('\n')[0]}</small>
        </label>)}</div>
        <section className="journey-summary"><div className="panel-title">Journeys ({journeys.length})</div>{journeys.map((journey, index) => <div key={index}>J{index}: {String(journey.journey_name || 'unnamed')} · {String(journey.journey_central_body || 'Sun')}</div>)}</section>
      </>}
    </main>
  </div>
}
