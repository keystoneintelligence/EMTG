import { describe, expect, it } from 'vitest'
import { jobProgressLabel } from './jobProgress'
import type { Job } from './types'

function job(status: string, progress: Record<string, unknown> = {}): Job {
  return {
    id: 'job', name: 'test', status, queue_position: 0,
    requested_cores: 10, effective_cores: 10, progress,
    config: {}, run_directory: 'run',
  }
}

describe('jobProgressLabel', () => {
  it('shows active evaluation instead of a resumability checkpoint yield', () => {
    expect(jobProgressLabel(job('running', {
      generation: 1,
      activity_status: 'evaluating',
      checkpoint_status: 'yielded',
    }))).toBe('generation 1 · evaluating')
  })

  it('uses the lifecycle state for user-visible stops', () => {
    expect(jobProgressLabel(job('paused', { checkpoint_status: 'interrupted' }))).toBe('paused')
    expect(jobProgressLabel(job('cancelled', { checkpoint_status: 'interrupted' }))).toBe('cancelled')
  })
})
