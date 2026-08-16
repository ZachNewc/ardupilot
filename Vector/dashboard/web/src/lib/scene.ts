/**
 * three.js scene for the 3D World page.
 *
 * Scaffold only. It builds the vehicle from the same config the rest of the
 * dashboard uses — arm count, azimuths, arm length and gimbal limits all come from
 * `vector.json` — so when telemetry is wired in later there is nothing to hardcode.
 * Nothing here reads live state yet.
 *
 * Kept out of the Vue component so the component stays a thin mount point and this
 * file can be tested or reused headlessly.
 *
 * Coordinates: three.js is Y-up and right-handed, while ArduPilot body frame is
 * X-forward, Y-right, Z-down. `bodyToScene` is the only place that conversion
 * happens.
 */

import {
  AmbientLight,
  Color,
  CylinderGeometry,
  DirectionalLight,
  Fog,
  Group,
  Mesh,
  MeshStandardMaterial,
  PerspectiveCamera,
  PolarGridHelper,
  Scene,
  SphereGeometry,
  TorusGeometry,
  Vector3,
  WebGLRenderer,
} from 'three'
import { OrbitControls } from 'three/addons/controls/OrbitControls.js'

import type { ArmConfig, FrameConfig } from './types'

const PALETTE = {
  frame: 0x2b3849,
  boom: 0x3d4d63,
  hub: 0x18212f,
  rotor: 0x5b6b82,
  arms: [0x3ba7ff, 0x38d39f, 0xf0b429, 0xc98bff],
  planned: 0x36414f,
}

/** ArduPilot body frame (X fwd, Y right, Z down) to three.js (X right, Y up, Z back). */
export const bodyToScene = (x: number, y: number, z: number): Vector3 =>
  new Vector3(y, -z, -x)

export interface ArmVisual {
  id: string
  group: Group
  /** The gimbal ring assembly, rotated when tilt is applied. */
  gimbal: Group
  /** Rotor discs, spun when telemetry provides RPM. */
  rotors: Mesh[]
}

export interface VectorScene {
  scene: Scene
  camera: PerspectiveCamera
  renderer: WebGLRenderer
  controls: OrbitControls
  vehicle: Group
  arms: ArmVisual[]
  /** Apply gimbal tilt in degrees. Ready for telemetry; nothing calls it yet. */
  setArmTilt(id: string, tiltOuterDeg: number, tiltInnerDeg: number): void
  /** Apply body attitude in degrees. */
  setAttitude(rollDeg: number, pitchDeg: number, yawDeg: number): void
  resize(width: number, height: number): void
  render(): void
  dispose(): void
}

const rad = (deg: number) => (deg * Math.PI) / 180

function buildArm(arm: ArmConfig, index: number, armLengthM: number): ArmVisual {
  const colour = arm.live ? PALETTE.arms[index % PALETTE.arms.length] : PALETTE.planned
  const group = new Group()
  group.name = `arm-${arm.id}`

  // Boom: a cylinder laid along the arm's azimuth, from hub to rotor.
  const boom = new Mesh(
    new CylinderGeometry(0.012, 0.012, armLengthM, 12),
    new MeshStandardMaterial({
      color: PALETTE.boom,
      metalness: 0.6,
      roughness: 0.5,
      transparent: !arm.live,
      opacity: arm.live ? 1 : 0.35,
    }),
  )
  // The cylinder is Y-aligned by default; lay it flat and point it outward.
  boom.rotation.z = Math.PI / 2
  boom.position.copy(bodyToScene(0, 0, 0)).lerp(bodyToScene(armLengthM, 0, 0), 0.5)
  const carrier = new Group()
  carrier.add(boom)
  carrier.rotation.y = -rad(arm.azimuthDeg)
  group.add(carrier)

  // Gimbal assembly sits at the end of the boom.
  const gimbal = new Group()
  gimbal.name = `gimbal-${arm.id}`
  const hub = bodyToScene(
    armLengthM * Math.cos(rad(arm.azimuthDeg)),
    armLengthM * Math.sin(rad(arm.azimuthDeg)),
    0,
  )
  gimbal.position.copy(hub)

  const ringMaterial = new MeshStandardMaterial({
    color: colour,
    metalness: 0.4,
    roughness: 0.45,
    transparent: !arm.live,
    opacity: arm.live ? 1 : 0.4,
  })

  // Outer ring (roll) and inner ring (pitch), drawn perpendicular so the two-axis
  // arrangement reads at a glance.
  const outerRing = new Mesh(new TorusGeometry(0.075, 0.005, 8, 32), ringMaterial)
  outerRing.rotation.y = Math.PI / 2
  const innerRing = new Mesh(new TorusGeometry(0.062, 0.004, 8, 32), ringMaterial)
  gimbal.add(outerRing, innerRing)

  // Coaxial pair: one disc above, one below.
  const rotors: Mesh[] = []
  const rotorMaterial = new MeshStandardMaterial({
    color: PALETTE.rotor,
    metalness: 0.3,
    roughness: 0.7,
    transparent: true,
    opacity: arm.live ? 0.5 : 0.2,
  })
  for (const offset of [0.045, -0.045]) {
    // 7 inch props: 0.089 m radius.
    const disc = new Mesh(new CylinderGeometry(0.089, 0.089, 0.004, 24), rotorMaterial)
    disc.position.y = offset
    gimbal.add(disc)
    rotors.push(disc)
  }

  const motorMaterial = new MeshStandardMaterial({ color: PALETTE.hub, metalness: 0.7, roughness: 0.4 })
  for (const offset of [0.025, -0.025]) {
    const can = new Mesh(new CylinderGeometry(0.017, 0.017, 0.026, 12), motorMaterial)
    can.position.y = offset
    gimbal.add(can)
  }

  group.add(gimbal)
  return { id: arm.id, group, gimbal, rotors }
}

export function createScene(
  canvas: HTMLCanvasElement,
  frame: FrameConfig,
  armConfigs: ArmConfig[],
): VectorScene {
  const scene = new Scene()
  scene.background = new Color(0x06080d)
  scene.fog = new Fog(0x06080d, 3.5, 11)

  const camera = new PerspectiveCamera(42, 1, 0.05, 100)
  camera.position.set(1.15, 0.85, 1.5)

  const renderer = new WebGLRenderer({ canvas, antialias: true })
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))

  const controls = new OrbitControls(camera, canvas)
  controls.enableDamping = true
  controls.dampingFactor = 0.08
  controls.minDistance = 0.6
  controls.maxDistance = 8
  // Stop below the floor so the vehicle is never viewed through the grid.
  controls.maxPolarAngle = Math.PI / 2 - 0.02
  controls.target.set(0, 0.05, 0)

  scene.add(new AmbientLight(0x8fa4c0, 0.55))
  const key = new DirectionalLight(0xffffff, 1.5)
  key.position.set(2.5, 4, 2)
  scene.add(key)
  const rim = new DirectionalLight(0x3ba7ff, 0.5)
  rim.position.set(-3, 1.5, -2)
  scene.add(rim)

  // Polar grid: rings at metre intervals with radials on the arm azimuths, which
  // makes the plus layout legible without labels.
  const grid = new PolarGridHelper(4, armConfigs.length * 2, 8, 64, 0x1e2836, 0x141c28)
  grid.position.y = -0.001
  scene.add(grid)

  const vehicle = new Group()
  vehicle.name = 'vehicle'

  const armLength = frame.armLengthM || frame.rotorDiagonalM / 2 || 0.405

  const body = new Mesh(
    new CylinderGeometry(0.07, 0.085, 0.055, 6),
    new MeshStandardMaterial({ color: PALETTE.frame, metalness: 0.55, roughness: 0.45 }),
  )
  vehicle.add(body)

  // Nose marker along the forward arm, so orientation is never ambiguous.
  const nose = new Mesh(
    new SphereGeometry(0.016, 12, 12),
    new MeshStandardMaterial({ color: 0xffffff, emissive: 0x333333 }),
  )
  nose.position.copy(bodyToScene(0.075, 0, -0.02))
  vehicle.add(nose)

  const arms = armConfigs.map((arm, index) => {
    const visual = buildArm(arm, index, armLength)
    vehicle.add(visual.group)
    return visual
  })

  vehicle.position.y = 0.35
  scene.add(vehicle)

  const byId = new Map(arms.map((visual) => [visual.id, visual]))

  return {
    scene,
    camera,
    renderer,
    controls,
    vehicle,
    arms,

    setArmTilt(id, tiltOuterDeg, tiltInnerDeg) {
      const visual = byId.get(id)
      if (!visual) return
      // Outer about body X (forward), inner about body Y (right). In scene axes
      // those are -Z and X respectively.
      visual.gimbal.rotation.set(rad(tiltInnerDeg), 0, -rad(tiltOuterDeg))
    },

    setAttitude(rollDeg, pitchDeg, yawDeg) {
      vehicle.rotation.set(rad(pitchDeg), -rad(yawDeg), -rad(rollDeg))
    },

    resize(width, height) {
      camera.aspect = width / Math.max(1, height)
      camera.updateProjectionMatrix()
      renderer.setSize(width, height, false)
    },

    render() {
      controls.update()
      renderer.render(scene, camera)
    },

    dispose() {
      controls.dispose()
      scene.traverse((object) => {
        if (object instanceof Mesh) {
          object.geometry.dispose()
          const material = object.material
          if (Array.isArray(material)) material.forEach((entry) => entry.dispose())
          else material.dispose()
        }
      })
      renderer.dispose()
    },
  }
}
