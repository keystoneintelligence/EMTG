export type Job = {
  id: string
  name: string
  status: string
  queue_position: number
  requested_cores: number
  effective_cores: number
  progress: Record<string, unknown>
  config: Record<string, unknown>
  run_directory: string
  error?: string
  updated_at?: string
}

export type SearchDefaults = {
  config: Record<string, unknown>
  ready: boolean
  missing: string[]
  runtime_root: string
  template: string
}

export type SearchEffortPreset = {
  id: string
  name: string
  description: string
  parallel_candidates: number
  population_size: number
  generations: number
  stall_generations: number
  trials: number
  solve_time_seconds: number
  nlp_major_iterations: number
  mbh_max_trials: number
  watchdog_seconds: number
}

export type SearchEffortPresetCollection = {
  default_id: string
  items: SearchEffortPreset[]
}

export type BodyOption = {
  name: string
  display_name: string
  short_name: string
  spice_id: number
  category: string
  kernel_files: string[]
  universe_file: string
}

export type BodyTrajectorySample = {
  epoch_mjd: number
  position_km: [number, number, number]
}

export type BodyTrajectory = {
  name: string
  display_name: string
  spice_id: number
  category: string
  coverage_status: 'covered' | 'partial' | 'uncovered' | 'error'
  coverage_start_mjd?: number
  coverage_end_mjd?: number
  error?: string
  samples: BodyTrajectorySample[]
}

export type BodyEphemerisResponse = {
  frame: string
  central_body: string
  observer_spice_id: number
  sample_count: number
  series: BodyTrajectory[]
}

export type CurrentBodyEphemerisResponse = BodyEphemerisResponse & {
  current_epoch_mjd: number
  current_utc: string
}

export type Solution = {
  id: string
  evaluation_key: string
  job_id: string
  candidate_id: string
  status: string
  feasible: boolean
  pareto: boolean
  fidelity?: string
  generation?: number
  start_body?: string
  end_body?: string
  sequence_text?: string
  launch_mjd?: number
  arrival_mjd?: number
  flight_time_days?: number
  propellant_used_kg?: number
  delivered_mass_kg?: number
  deterministic_delta_v_km_s?: number
  thrust_min_n?: number
  thrust_max_n?: number
  duty_cycle?: number
  active_engines?: number
  bus_power_kw?: number
}

export type TrajectorySample = {
  epoch_mjd: number
  tdb_minus_utc_seconds?: number
  position_km: [number, number, number]
  velocity_km_s?: [number, number, number]
  event_type?: string
  location?: string
  mass_kg?: number
  control?: [number, number, number]
  thrust_n?: [number, number, number]
  thrust_magnitude_n?: number
  available_thrust_n?: number
  mass_flow_rate_kg_s?: number
  active_engines?: number
  active_power_kw?: number
}

export type Trajectory = {
  solution_id: string
  detail: 'events' | 'dense'
  frame: string
  source_frame?: string
  central_body?: string
  time_system: string
  source_time_system?: string
  transformation_applied?: string
  samples: TrajectorySample[]
  original_count: number
  returned_count: number
  materialization_status: string
}

export type OptionField = {
  scope: 'mission' | 'journey'
  group: string
  name: string
  data_type: string
  default: unknown
  lower?: unknown
  upper?: unknown
  description: string
  choices: Array<{ value: number; label: string }>
  aliases: string[]
  applicable_solvers: number[]
}
