import type { BodyOption, BodyTrajectory, Job, OptionField, SearchDefaults, Solution, Trajectory } from './types'

const query = new URLSearchParams(window.location.search)
const suppliedToken = query.get('access_token')
if (suppliedToken) localStorage.setItem('emtg-studio-token', suppliedToken)
export const token = suppliedToken || localStorage.getItem('emtg-studio-token') || ''

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      'X-EMTG-Token': token,
      ...(init?.headers || {}),
    },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new Error(body.detail || response.statusText)
  }
  return response.json()
}

export const api = {
  jobs: () => request<{ items: Job[]; global_core_limit: number }>('/api/v1/jobs'),
  searchDefaults: () => request<SearchDefaults>('/api/v1/search/defaults'),
  bodies: () => request<{ items: BodyOption[]; count: number; ready: boolean; central_body: string; universe_file: string; kernel_files: string[]; error?: string }>('/api/v1/bodies'),
  bodyEphemerides: (names: string[], startMjd: number, endMjd: number, points = 360) => {
    const filters = new URLSearchParams({ start_mjd: String(startMjd), end_mjd: String(endMjd), points: String(points), frame: 'J2000' })
    names.forEach(name => filters.append('names', name))
    return request<{ frame: string; central_body: string; observer_spice_id: number; sample_count: number; series: BodyTrajectory[] }>(`/api/v1/ephemeris/bodies?${filters}`)
  },
  createJob: (body: unknown) => request<Job>('/api/v1/jobs', { method: 'POST', body: JSON.stringify(body) }),
  jobAction: (id: string, action: string) => request<Job>(`/api/v1/jobs/${id}/${action}`, { method: 'POST' }),
  deleteJob: (id: string) => request<{ deleted_job_id: string; deleted_solutions: number; deleted_trajectories: number }>(`/api/v1/jobs/${id}`, { method: 'DELETE' }),
  setJobCores: (id: string, requested_cores: number) => request<Job>(`/api/v1/jobs/${id}/resources`, {
    method: 'PATCH', body: JSON.stringify({ requested_cores }),
  }),
  setGlobalCores: (global_core_limit: number) => request<{ global_core_limit: number }>('/api/v1/resources', {
    method: 'PATCH', body: JSON.stringify({ global_core_limit }),
  }),
  solutions: (filters: URLSearchParams) => request<{ items: Solution[]; total: number }>(`/api/v1/solutions?${filters}`),
  solution: (id: string) => request<Record<string, unknown>>(`/api/v1/solutions/${id}`),
  trajectory: (id: string) => request<Trajectory>(`/api/v1/solutions/${id}/trajectory?detail=auto&max_points=10000`),
  materialize: (id: string) => request<{ status: string }>(`/api/v1/solutions/${id}/materialize`, { method: 'POST' }),
  optionSchema: () => request<{ items: OptionField[] }>('/api/v1/options/schema'),
  openOptions: (path: string) => request<{ path: string; mission: Record<string, unknown>; journeys: Record<string, unknown>[] }>('/api/v1/options/open', {
    method: 'POST', body: JSON.stringify({ path }),
  }),
  saveOptions: (body: unknown) => request<{ saved: string }>('/api/v1/options/save', { method: 'POST', body: JSON.stringify(body) }),
}
