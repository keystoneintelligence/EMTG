import { describe, expect, it } from 'vitest'
import { applySearchEffort, formatDuration, searchEffortEstimate } from './searchEffort'
import type { SearchEffortPreset } from './types'

const production: SearchEffortPreset = {
  id: 'production', name: 'Production', description: 'Production search',
  parallel_candidates: 10, population_size: 20, generations: 4,
  stall_generations: 4, trials: 1, solve_time_seconds: 600,
  nlp_major_iterations: 5000, mbh_max_trials: 200000, watchdog_seconds: 720,
}

describe('search effort presets', () => {
  it('applies all effort controls while preserving unrelated campaign settings', () => {
    const configured = applySearchEffort({
      root_seed: 7,
      algorithm: { tournament_size: 2, population_size: 4 },
      evaluator: { type: 'emtg', budget: { nlp_solver_type: 2, quiet_nlp: 1 } },
      workers: { count: 2, infrastructure_retries: 1 },
    }, production)
    expect(configured.algorithm).toEqual({
      tournament_size: 2, population_size: 20, generations: 4,
      stall_generations: 4, trials: 1,
    })
    expect(configured.evaluator).toEqual({
      type: 'emtg', timeout_seconds: 720,
      budget: {
        nlp_solver_type: 2, quiet_nlp: 1, inner_loop: 'mbh',
        mbh_max_run_time: 600, mbh_max_trials: 200000,
        nlp_max_run_time: 600, nlp_major_iterations: 5000,
      },
    })
    expect(configured.workers).toEqual({ count: 10, infrastructure_retries: 1 })
    expect(configured.root_seed).toBe(7)
  })

  it('reports the generation-barrier candidate and wall-time upper bounds', () => {
    expect(searchEffortEstimate(production)).toEqual({
      proposedCandidates: 100, activeWorkers: 10, waves: 10, worstCaseSeconds: 6000,
    })
    expect(formatDuration(6000)).toBe('1.7 h')
  })
})
