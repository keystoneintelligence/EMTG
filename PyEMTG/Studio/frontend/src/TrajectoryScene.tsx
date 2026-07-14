import { Canvas } from '@react-three/fiber'
import { Html, Line, OrbitControls, Stars } from '@react-three/drei'
import { useMemo } from 'react'
import * as THREE from 'three'
import type { BodyTrajectory, Solution, Trajectory } from './types'
import { interpolateTrajectorySamples, trajectoryDisplaySegments } from './trajectoryInterpolation'

const COLORS = ['#53d8fb', '#ffb454', '#a78bfa', '#63e6be', '#ff6b8a', '#f7df72']
const BODY_COLORS: Record<string, string> = {
  Earth: '#55a9ff', Venus: '#e8bc72', Mercury: '#a8a39c', Mars_system: '#ed765f',
  Jupiter_system: '#d5ae83', Saturn_system: '#d9c58e', Uranus_system: '#85d4dc',
  Neptune_system: '#668cff', Pluto_system: '#c8b3a0', Moon: '#d9e0e5', A20136163: '#ff9f43',
}

function bodyIdentity(value: string): string {
  return value.toLowerCase().replace(/[ _-]?system$/, '')
}

function ReferenceGrid({ earth }: { earth?: BodyTrajectory }) {
  const quaternion = useMemo(() => {
    // GridHelper is created in its local XZ plane, whose normal is +Y.
    // Average r_i x r_(i+1) over the heliocentric Earth track to recover
    // Earth's orbital angular-momentum direction in the active ICRF scene.
    const normal = new THREE.Vector3()
    const first = new THREE.Vector3()
    const second = new THREE.Vector3()
    const segmentNormal = new THREE.Vector3()
    const samples = earth?.samples || []
    for (let index = 1; index < samples.length; index += 1) {
      first.fromArray(samples[index - 1].position_km)
      second.fromArray(samples[index].position_km)
      segmentNormal.crossVectors(first, second)
      if (segmentNormal.lengthSq() > 1e-16) normal.add(segmentNormal.normalize())
    }
    // With no Earth coverage, retain a deterministic ICRF-equatorial fallback
    // instead of allowing an arbitrary Three.js default plane.
    if (normal.lengthSq() <= 1e-16) normal.set(0, 0, 1)
    else normal.normalize()
    return new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), normal)
  }, [earth])
  return <gridHelper args={[30, 30, '#1b3850', '#0b1d2b']} quaternion={quaternion} />
}

function TrajectoryPath({ trajectory, color, epoch, scale, startBody, endBody }: { trajectory: Trajectory; color: string; epoch: number; scale: number; startBody?: string; endBody?: string }) {
  const coastColor = useMemo(
    () => new THREE.Color(color).lerp(new THREE.Color('#c0d0da'), 0.62).getStyle(),
    [color],
  )
  const { points, segments, marker } = useMemo(() => {
    if (!trajectory.samples.length) return { points: [], segments: [], marker: [0, 0, 0] as [number, number, number] }
    const renderSamples = interpolateTrajectorySamples(trajectory)
    const convertedSamples = renderSamples.map(sample => ({
      ...sample,
      position_km: sample.position_km.map(component => component * scale) as [number, number, number],
    }))
    const converted = convertedSamples.map(sample => sample.position_km)
    let upper = renderSamples.findIndex(sample => sample.epoch_mjd >= epoch)
    if (upper < 0) upper = converted.length - 1
    if (upper === 0) return {
      points: converted,
      segments: trajectoryDisplaySegments(convertedSamples),
      marker: converted[0],
    }
    const lower = upper - 1
    const interval = renderSamples[upper].epoch_mjd - renderSamples[lower].epoch_mjd
    const fraction = interval > 0 ? Math.max(0, Math.min(1, (epoch - renderSamples[lower].epoch_mjd) / interval)) : 1
    const marker = converted[lower].map(
      (component, axis) => component + (converted[upper][axis] - component) * fraction,
    ) as [number, number, number]
    return { points: converted, segments: trajectoryDisplaySegments(convertedSamples), marker }
  }, [trajectory, epoch, scale])
  if (points.length < 2) return null
  return <group>
    {segments.map((segment, index) => <Line
      key={`${segment.mode}-${index}`}
      points={segment.points}
      color={segment.mode === 'burn' ? color : segment.mode === 'coast' ? coastColor : color}
      transparent
      opacity={segment.mode === 'burn' ? 0.98 : segment.mode === 'coast' ? 0.58 : 0.76}
      lineWidth={segment.mode === 'burn' ? 3.5 : segment.mode === 'coast' ? 1.45 : 2.1}
    />)}
    <mesh position={marker}><sphereGeometry args={[0.16, 16, 16]} /><meshBasicMaterial color={color} /></mesh>
    <mesh position={points[0]}><sphereGeometry args={[0.09, 12, 12]} /><meshBasicMaterial color="#6eb9ff" /><Html center className="encounter-label">Depart {startBody || 'start'}</Html></mesh>
    <mesh position={points[points.length - 1]}><sphereGeometry args={[0.1, 12, 12]} /><meshBasicMaterial color="#ff9b62" /><Html center className="encounter-label">Arrive {endBody || 'end'}</Html></mesh>
  </group>
}

function BodyPath({ body, epoch, scale, color, highlighted }: { body: BodyTrajectory; epoch: number; scale: number; color: string; highlighted: boolean }) {
  const { points, marker } = useMemo(() => {
    const converted = body.samples.map(sample => sample.position_km.map(component => component * scale) as [number, number, number])
    if (!converted.length) return { points: [], marker: null as [number, number, number] | null }
    const inCoverage = epoch >= body.samples[0].epoch_mjd && epoch <= body.samples[body.samples.length - 1].epoch_mjd
    if (!inCoverage) return { points: converted, marker: null as [number, number, number] | null }
    let upper = body.samples.findIndex(sample => sample.epoch_mjd >= epoch)
    if (upper < 0) upper = converted.length - 1
    const lower = Math.max(0, upper - 1)
    const interval = body.samples[upper].epoch_mjd - body.samples[lower].epoch_mjd
    const fraction = interval > 0 ? Math.max(0, Math.min(1, (epoch - body.samples[lower].epoch_mjd) / interval)) : 1
    const marker = converted[lower].map(
      (component, axis) => component + (converted[upper][axis] - component) * fraction,
    ) as [number, number, number]
    return { points: converted, marker }
  }, [body, epoch, scale])
  if (points.length < 2) return null
  const radius = body.category === 'asteroid' ? 0.11 : body.category === 'moon' ? 0.13 : 0.18
  return <group>
    <Line
      points={points}
      color={highlighted ? color : '#9badb7'}
      transparent
      opacity={highlighted ? 0.48 : 0.24}
      lineWidth={highlighted ? 1.35 : 0.8}
      dashed
      dashSize={0.17}
      gapSize={0.12}
    />
    {marker && <mesh position={marker}>
      <sphereGeometry args={[radius, 18, 18]} /><meshBasicMaterial color={color} />
      <Html center className="body-label">{body.display_name}</Html>
    </mesh>}
  </group>
}

export function TrajectoryScene({
  trajectories, selected, bodyTrajectories, epoch,
}: { trajectories: Map<string, Trajectory>; selected: Solution[]; bodyTrajectories: BodyTrajectory[]; epoch: number }) {
  const scale = useMemo(() => {
    const positions = [
      ...[...trajectories.values()].flatMap(value => value.samples.map(sample => sample.position_km)),
      ...bodyTrajectories.flatMap(value => value.samples.map(sample => sample.position_km)),
    ]
    const extent = Math.max(1, ...positions.flatMap(value => value.map(component => Math.abs(component))))
    return 14 / extent
  }, [trajectories, bodyTrajectories])
  const earthTrack = bodyTrajectories.find(body => body.name === 'Earth')
  const endpointBodies = useMemo(
    () => new Set(
      selected
        .flatMap(solution => [solution.start_body, solution.end_body])
        .filter((value): value is string => Boolean(value))
        .map(bodyIdentity),
    ),
    [selected],
  )
  return <Canvas camera={{ position: [16, 12, 18], fov: 48 }} frameloop="always">
    <color attach="background" args={['#040810']} />
    <ambientLight intensity={0.8} />
    <pointLight position={[0, 0, 0]} intensity={3} color="#fff3c4" />
    <Stars radius={90} depth={30} count={2400} factor={2} saturation={0} fade speed={0.25} />
    <ReferenceGrid earth={earthTrack} />
    <mesh>
      <sphereGeometry args={[0.32, 24, 24]} /><meshBasicMaterial color="#ffd36b" />
      <Html center className="body-label">Sun</Html>
    </mesh>
    {bodyTrajectories.map((body, index) => <BodyPath key={body.name} body={body} epoch={epoch} scale={scale} color={BODY_COLORS[body.name] || COLORS[index % COLORS.length]} highlighted={endpointBodies.has(bodyIdentity(body.name)) || endpointBodies.has(bodyIdentity(body.display_name))} />)}
    {selected.map((solution, index) => {
      const trajectory = trajectories.get(solution.id)
      return trajectory ? <TrajectoryPath key={solution.id} trajectory={trajectory} color={COLORS[index % COLORS.length]} epoch={epoch} scale={scale} startBody={solution.start_body} endBody={solution.end_body} /> : null
    })}
    <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
  </Canvas>
}
