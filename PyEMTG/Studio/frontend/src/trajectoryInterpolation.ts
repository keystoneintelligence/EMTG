import type { Trajectory, TrajectorySample } from './types'

export type RenderTrajectorySample = {
  epoch_mjd: number
  position_km: [number, number, number]
  propulsion_mode: PropulsionMode
}

export type PropulsionMode = 'burn' | 'coast' | 'unknown'

export type TrajectoryDisplaySegment = {
  mode: PropulsionMode
  points: [number, number, number][]
}

function hasVector(value: unknown): value is [number, number, number] {
  return Array.isArray(value) && value.length === 3 && value.every(component => Number.isFinite(component))
}

function vectorMagnitude(value: [number, number, number]): number {
  return Math.hypot(...value)
}

export function propulsionMode(sample: TrajectorySample): PropulsionMode {
  if (Number.isFinite(sample.thrust_magnitude_n)) {
    return Math.abs(sample.thrust_magnitude_n!) > 1e-9 ? 'burn' : 'coast'
  }
  if (hasVector(sample.thrust_n)) return vectorMagnitude(sample.thrust_n) > 1e-9 ? 'burn' : 'coast'
  if (Number.isFinite(sample.mass_flow_rate_kg_s)) {
    return Math.abs(sample.mass_flow_rate_kg_s!) > 1e-12 ? 'burn' : 'coast'
  }
  if (Number.isFinite(sample.active_engines)) return sample.active_engines! > 0 ? 'burn' : 'coast'
  if (hasVector(sample.control)) return vectorMagnitude(sample.control) > 1e-9 ? 'burn' : 'coast'
  return 'unknown'
}

export function trajectoryDisplaySegments(samples: RenderTrajectorySample[]): TrajectoryDisplaySegment[] {
  if (samples.length < 2) return []
  const output: TrajectoryDisplaySegment[] = []
  for (let index = 1; index < samples.length; index += 1) {
    const first = samples[index - 1]
    const second = samples[index]
    const mode = second.propulsion_mode === 'unknown' ? first.propulsion_mode : second.propulsion_mode
    const previous = output[output.length - 1]
    if (previous?.mode === mode) previous.points.push([...second.position_km])
    else output.push({ mode, points: [[...first.position_km], [...second.position_km]] })
  }
  return output
}

function linearPosition(first: TrajectorySample, second: TrajectorySample, fraction: number): [number, number, number] {
  return first.position_km.map(
    (component, axis) => component + (second.position_km[axis] - component) * fraction,
  ) as [number, number, number]
}

function hermitePosition(first: TrajectorySample, second: TrajectorySample, fraction: number, seconds: number): [number, number, number] {
  const squared = fraction * fraction
  const cubed = squared * fraction
  const h00 = 2 * cubed - 3 * squared + 1
  const h10 = cubed - 2 * squared + fraction
  const h01 = -2 * cubed + 3 * squared
  const h11 = cubed - squared
  return first.position_km.map((component, axis) => (
    h00 * component
    + h10 * first.velocity_km_s![axis] * seconds
    + h01 * second.position_km[axis]
    + h11 * second.velocity_km_s![axis] * seconds
  )) as [number, number, number]
}

/**
 * Create display samples for sparse EMTG event trajectories.
 *
 * EMTG event records carry both position and velocity, so cubic Hermite
 * segments reconstruct a smooth, time-aware path while preserving every
 * event position exactly. Dense propagated ephemerides are already sampled
 * for display and are returned without interpolation.
 */
export function interpolateTrajectorySamples(trajectory: Trajectory, targetSamples = 1200): RenderTrajectorySample[] {
  const samples = trajectory.samples
  if (samples.length < 2 || trajectory.detail === 'dense') {
    return samples.map(sample => ({
      epoch_mjd: sample.epoch_mjd,
      position_km: [...sample.position_km],
      propulsion_mode: propulsionMode(sample),
    }))
  }
  const totalDays = Math.max(samples[samples.length - 1].epoch_mjd - samples[0].epoch_mjd, 1e-12)
  const output: RenderTrajectorySample[] = [{
    epoch_mjd: samples[0].epoch_mjd,
    position_km: [...samples[0].position_km],
    propulsion_mode: propulsionMode(samples[0]),
  }]
  for (let index = 1; index < samples.length; index += 1) {
    const first = samples[index - 1]
    const second = samples[index]
    const intervalDays = second.epoch_mjd - first.epoch_mjd
    if (intervalDays <= 0) {
      output.push({
        epoch_mjd: second.epoch_mjd,
        position_km: [...second.position_km],
        propulsion_mode: propulsionMode(second),
      })
      continue
    }
    const subdivisions = Math.max(2, Math.min(48, Math.ceil(targetSamples * intervalDays / totalDays)))
    const useHermite = hasVector(first.velocity_km_s) && hasVector(second.velocity_km_s)
    const intervalSeconds = intervalDays * 86400
    for (let step = 1; step <= subdivisions; step += 1) {
      const fraction = step / subdivisions
      output.push({
        epoch_mjd: first.epoch_mjd + intervalDays * fraction,
        position_km: useHermite
          ? hermitePosition(first, second, fraction, intervalSeconds)
          : linearPosition(first, second, fraction),
        propulsion_mode: fraction < 0.5 ? propulsionMode(first) : propulsionMode(second),
      })
    }
  }
  return output
}
