import type { Job } from './types'

function generationLabel(progress: Record<string, unknown>): string {
  const generation = Number(progress.generation)
  return Number.isInteger(generation) && generation >= 0 ? `generation ${generation} · ` : ''
}

export function jobProgressLabel(job: Job): string {
  if (job.status === 'running') {
    const activity = String(job.progress.activity_status || 'evaluating')
    return `${generationLabel(job.progress)}${activity}`
  }
  if (job.status === 'pausing') return 'pausing'
  if (job.status === 'paused') return 'paused'
  if (job.status === 'completed') return 'complete'
  if (job.status === 'failed') return 'failed'
  if (job.status === 'cancelled') return 'cancelled'
  if (job.status === 'queued') return 'waiting to start'
  return 'not started'
}
