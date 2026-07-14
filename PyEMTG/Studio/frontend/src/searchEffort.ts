import type { SearchEffortPreset } from './types'

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

export function applySearchEffort(
  source: Record<string, unknown>, preset: SearchEffortPreset,
): Record<string, unknown> {
  const value = structuredClone(source)
  value.algorithm = {
    ...record(value.algorithm),
    population_size: preset.population_size,
    generations: preset.generations,
    stall_generations: preset.stall_generations,
    trials: preset.trials,
  }
  const evaluator = record(value.evaluator)
  evaluator.timeout_seconds = preset.watchdog_seconds
  evaluator.budget = {
    ...record(evaluator.budget),
    inner_loop: 'mbh',
    mbh_max_run_time: preset.solve_time_seconds,
    mbh_max_trials: preset.mbh_max_trials,
    nlp_max_run_time: preset.solve_time_seconds,
    nlp_major_iterations: preset.nlp_major_iterations,
  }
  value.evaluator = evaluator
  value.workers = {
    ...record(value.workers),
    count: preset.parallel_candidates,
  }
  return value
}

export function searchEffortEstimate(preset: SearchEffortPreset) {
  const proposedCandidates = preset.population_size * (preset.generations + 1) * preset.trials
  const activeWorkers = Math.min(preset.parallel_candidates, preset.population_size)
  const waves = Math.ceil(preset.population_size / activeWorkers) * (preset.generations + 1) * preset.trials
  return {
    proposedCandidates,
    activeWorkers,
    waves,
    worstCaseSeconds: waves * preset.solve_time_seconds,
  }
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds} s`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  const hours = seconds / 3600
  return `${hours < 10 ? hours.toFixed(1) : Math.round(hours)} h`
}
