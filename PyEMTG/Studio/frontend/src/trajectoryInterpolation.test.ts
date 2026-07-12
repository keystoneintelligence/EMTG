import { describe, expect, it } from 'vitest'
import type { Trajectory } from './types'
import { interpolateTrajectorySamples } from './trajectoryInterpolation'

function trajectory(detail: 'events' | 'dense' = 'events'): Trajectory {
  return {
    solution_id: 'test', detail, frame: 'J2000/ICRF', time_system: 'TDB',
    original_count: 2, returned_count: 2, materialization_status: 'available',
    samples: [
      { epoch_mjd: 60000, position_km: [1, 0, 0], velocity_km_s: [0, 1, 0] },
      { epoch_mjd: 60000 + 1 / 86400, position_km: [0, 1, 0], velocity_km_s: [-1, 0, 0] },
    ],
  }
}

describe('interpolateTrajectorySamples', () => {
  it('uses time-scaled Hermite states and preserves event endpoints', () => {
    const samples = interpolateTrajectorySamples(trajectory(), 4)
    expect(samples).toHaveLength(5)
    expect(samples[0].position_km).toEqual([1, 0, 0])
    expect(samples[2].position_km[0]).toBeCloseTo(0.625, 7)
    expect(samples[2].position_km[1]).toBeCloseTo(0.625, 7)
    expect(samples[4].position_km).toEqual([0, 1, 0])
  })

  it('does not resample dense propagated ephemerides', () => {
    const samples = interpolateTrajectorySamples(trajectory('dense'), 100)
    expect(samples).toHaveLength(2)
    expect(samples.map(sample => sample.position_km)).toEqual([[1, 0, 0], [0, 1, 0]])
  })

  it('falls back to endpoint-preserving linear interpolation without velocity states', () => {
    const value = trajectory()
    value.samples.forEach(sample => { delete sample.velocity_km_s })
    const samples = interpolateTrajectorySamples(value, 4)
    expect(samples[2].position_km).toEqual([0.5, 0.5, 0])
  })
})
