import { Canvas } from '@react-three/fiber'
import { Html, Line, OrbitControls, Stars } from '@react-three/drei'
import { useMemo } from 'react'
import * as THREE from 'three'
import type { BodyTrajectory, Solution, Trajectory } from './types'
import { interpolateTrajectorySamples } from './trajectoryInterpolation'

const COLORS = ['#53d8fb', '#ffb454', '#a78bfa', '#63e6be', '#ff6b8a', '#f7df72']
const BODY_COLORS: Record<string, string> = {
  Earth: '#55a9ff', Venus: '#e8bc72', Mercury: '#a8a39c', Mars_system: '#ed765f',
  Jupiter_system: '#d5ae83', Saturn_system: '#d9c58e', Uranus_system: '#85d4dc',
  Neptune_system: '#668cff', Pluto_system: '#c8b3a0', Moon: '#d9e0e5', A20136163: '#ff9f43',
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
  const { points, elapsed, marker } = useMemo(() => {
    if (!trajectory.samples.length) return { points: [], elapsed: [], marker: [0, 0, 0] as [number, number, number] }
    const renderSamples = interpolateTrajectorySamples(trajectory)
    const converted = renderSamples.map(sample => sample.position_km.map(component => component * scale) as [number, number, number])
    let upper = renderSamples.findIndex(sample => sample.epoch_mjd >= epoch)
    if (upper < 0) upper = converted.length - 1
    if (upper === 0) return { points: converted, elapsed: [converted[0]], marker: converted[0] }
    const lower = upper - 1
    const interval = renderSamples[upper].epoch_mjd - renderSamples[lower].epoch_mjd
    const fraction = interval > 0 ? Math.max(0, Math.min(1, (epoch - renderSamples[lower].epoch_mjd) / interval)) : 1
    const marker = converted[lower].map(
      (component, axis) => component + (converted[upper][axis] - component) * fraction,
    ) as [number, number, number]
    return { points: converted, elapsed: [...converted.slice(0, upper), marker], marker }
  }, [trajectory, epoch, scale])
  if (points.length < 2) return null
  return <group>
    <Line points={points} color={color} transparent opacity={0.28} lineWidth={1.2} />
    {elapsed.length >= 2 && <Line points={elapsed} color={color} lineWidth={2.8} />}
    <mesh position={marker}><sphereGeometry args={[0.16, 16, 16]} /><meshBasicMaterial color={color} /></mesh>
    <mesh position={points[0]}><sphereGeometry args={[0.09, 12, 12]} /><meshBasicMaterial color="#6eb9ff" /><Html center className="encounter-label">Depart {startBody || 'start'}</Html></mesh>
    <mesh position={points[points.length - 1]}><sphereGeometry args={[0.1, 12, 12]} /><meshBasicMaterial color="#ff9b62" /><Html center className="encounter-label">Arrive {endBody || 'end'}</Html></mesh>
  </group>
}

function BodyPath({ body, epoch, scale, color }: { body: BodyTrajectory; epoch: number; scale: number; color: string }) {
  const { points, tail, marker } = useMemo(() => {
    const converted = body.samples.map(sample => sample.position_km.map(component => component * scale) as [number, number, number])
    if (!converted.length) return { points: [], tail: [], marker: null as [number, number, number] | null }
    const inCoverage = epoch >= body.samples[0].epoch_mjd && epoch <= body.samples[body.samples.length - 1].epoch_mjd
    if (!inCoverage) return { points: converted, tail: [], marker: null as [number, number, number] | null }
    let index = body.samples.findIndex(sample => sample.epoch_mjd >= epoch)
    if (index < 0) index = converted.length - 1
    const tailLength = Math.max(8, Math.round(converted.length * 0.18))
    return { points: converted, tail: converted.slice(Math.max(0, index - tailLength), index + 1), marker: converted[index] }
  }, [body, epoch, scale])
  if (points.length < 2) return null
  const radius = body.category === 'asteroid' ? 0.11 : body.category === 'moon' ? 0.13 : 0.18
  return <group>
    <Line points={points} color={color} transparent opacity={0.32} lineWidth={1.2} />
    {tail.length >= 2 && <Line points={tail} color={color} transparent opacity={0.95} lineWidth={3.4} />}
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
    {bodyTrajectories.map((body, index) => <BodyPath key={body.name} body={body} epoch={epoch} scale={scale} color={BODY_COLORS[body.name] || COLORS[index % COLORS.length]} />)}
    {selected.map((solution, index) => {
      const trajectory = trajectories.get(solution.id)
      return trajectory ? <TrajectoryPath key={solution.id} trajectory={trajectory} color={COLORS[index % COLORS.length]} epoch={epoch} scale={scale} startBody={solution.start_body} endBody={solution.end_body} /> : null
    })}
    <OrbitControls makeDefault enableDamping dampingFactor={0.08} />
  </Canvas>
}
