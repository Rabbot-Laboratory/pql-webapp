<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import URDFLoader, { type URDFJoint, type URDFRobot } from 'urdf-loader';
import { XacroLoader } from 'xacro-parser';

import type { ImuOrientation, ImuQuaternion, LegId, LegPreview } from '@/types/control';
import { imuQuaternionToScene } from '@/utils/imuFrame';

const props = defineProps<{
  legs: LegPreview[];
  focusedLegId: LegId;
  supportingLegIds?: LegId[];
  imuQuaternion?: ImuQuaternion | null;
  imuOrientation?: ImuOrientation | null;
}>();

const DESCRIPTION_PACKAGE_URL = '/robot-description/pql-a00/';
const XACRO_URL = `${DESCRIPTION_PACKAGE_URL}urdf/pql-a00.xacro`;
const XACRO_WORKING_PATH = `${DESCRIPTION_PACKAGE_URL}urdf/`;
const COLORED_MODEL_URL = `${DESCRIPTION_PACKAGE_URL}meshes/PQL01_002_assy_colored.glb`;

const LEG_JOINTS: Record<
  LegId,
  {
    fixed: string;
    hip: string;
    knee: string;
    links: string[];
  }
> = {
  front_right: {
    fixed: 'rev_fr1',
    hip: 'rev_fr2',
    knee: 'rev_fr3',
    links: ['PQL-LF00-FR_v2_1', 'PQL01-LU00-A1-FR_v1_1', 'PQL-LD00-FR_v1_1'],
  },
  front_left: {
    fixed: 'rev_fl1',
    hip: 'rev_fl2',
    knee: 'rev_fl3',
    links: ['PQL-LF00-FL_v2_2', 'PQL01-LU00-A1-FL_v1_1', 'PQL-LD00-FL_v1_1'],
  },
  rear_right: {
    fixed: 'rev_rr1',
    hip: 'rev_rr2',
    knee: 'rev_rr3',
    links: ['PQL-LF00-FL_v2_1', 'PQL01-LU00-A1-RR_v1_2', 'PQL-LD00-RR_v1_1'],
  },
  rear_left: {
    fixed: 'rev_rl1',
    hip: 'rev_rl2',
    knee: 'rev_rl3',
    links: ['PQL-LF00-RL_v1_1', 'PQL01-LU00-A1-RL_v1_1', 'PQL-LD00-RL_v1_1'],
  },
};

const stageRef = ref<HTMLDivElement | null>(null);
const loading = ref(true);
const error = ref('');

const STAGE_BACKGROUND = 0x0b111b;
const scene = new THREE.Scene();
scene.background = new THREE.Color(STAGE_BACKGROUND);
const robotRoot = new THREE.Group();
scene.add(robotRoot);

const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 10);
camera.up.set(0, 0, 1);
camera.position.set(0.7, -0.95, 0.55);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.setClearColor(STAGE_BACKGROUND, 1);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.25));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = false;

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = false;
controls.target.set(0, 0, 0);
controls.minDistance = 0.3;
controls.maxDistance = 2.6;

const ambientLight = new THREE.AmbientLight('#ffffff', 0.72);
scene.add(ambientLight);

const hemisphereLight = new THREE.HemisphereLight('#ffffff', '#8290a3', 1.5);
scene.add(hemisphereLight);

const keyLight = new THREE.DirectionalLight('#ffffff', 1.65);
keyLight.position.set(1.3, -1.1, 1.7);
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight('#dbeafe', 1.0);
fillLight.position.set(-0.9, 1.0, 0.9);
scene.add(fillLight);

let resizeObserver: ResizeObserver | null = null;
let robot: URDFRobot | null = null;

interface CylinderBinding {
  name: string;
  bodyMode: 'fixed' | 'pivot';
  bodyRig: THREE.Group;
  rodRig: THREE.Group;
  movingAnchor: THREE.Object3D;
  zeroDirectionRoot: THREE.Vector3;
  zeroRigQuaternionRoot: THREE.Quaternion;
  zeroLength: number;
  zeroRodPosition: THREE.Vector3;
  rodAxisInRig: THREE.Vector3;
}

const cylinderBindings: CylinderBinding[] = [];
const cylinderRootInverse = new THREE.Matrix4();
const cylinderParentInRoot = new THREE.Matrix4();
const cylinderDesiredInRoot = new THREE.Matrix4();
const cylinderLocalMatrix = new THREE.Matrix4();
const cylinderPosition = new THREE.Vector3();
const cylinderMovingPosition = new THREE.Vector3();
const cylinderDirection = new THREE.Vector3();
const cylinderFixedAxis = new THREE.Vector3();
const cylinderDeltaQuaternion = new THREE.Quaternion();
const cylinderDesiredQuaternion = new THREE.Quaternion();
const cylinderUnitScale = new THREE.Vector3(1, 1, 1);

interface ContactRipple {
  group: THREE.Group;
  rings: THREE.Mesh<THREE.RingGeometry, THREE.MeshBasicMaterial>[];
}

const contactFootObjects = new Map<LegId, THREE.Object3D>();
const contactRipples = new Map<LegId, ContactRipple>();
const contactBounds = new THREE.Box3();
const contactCenter = new THREE.Vector3();
const RIPPLE_FRAME_INTERVAL_MS = 1000 / 30;
let rippleAnimationFrame: number | null = null;
let rippleLastFrameAt = 0;

for (const legId of Object.keys(LEG_JOINTS) as LegId[]) {
  const group = new THREE.Group();
  group.name = `${legId}_contact_ripple`;
  group.visible = false;
  const rings = [0, 1, 2].map((index) => {
    const material = new THREE.MeshBasicMaterial({
      color: 0x38bdf8,
      transparent: true,
      opacity: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
      toneMapped: false,
    });
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.014, 0.0165, 64), material);
    ring.name = `${legId}_contact_ring_${index}`;
    ring.renderOrder = 10;
    group.add(ring);
    return ring;
  });
  scene.add(group);
  contactRipples.set(legId, { group, rings });
}

const poseSignature = computed(() =>
  props.legs
    .map(
      (leg) =>
        `${leg.leg_id}:${leg.fixed_joint_angle_rad.toFixed(4)}:${leg.hip.angle_rad.toFixed(4)}:${leg.knee.angle_rad.toFixed(4)}`,
    )
    .join('|'),
);

const imuSignature = computed(() => {
  if (props.imuQuaternion) {
    return [
      props.imuQuaternion.w.toFixed(4),
      props.imuQuaternion.x.toFixed(4),
      props.imuQuaternion.y.toFixed(4),
      props.imuQuaternion.z.toFixed(4),
    ].join(':');
  }
  if (!props.imuOrientation) {
    return 'none';
  }
  return `${props.imuOrientation.roll_deg.toFixed(3)}:${props.imuOrientation.pitch_deg.toFixed(3)}`;
});

const supportSignature = computed(() => [...(props.supportingLegIds ?? [])].sort().join('|'));

function configureRobotMaterials(nextRobot: URDFRobot): void {
  const sourceMaterialSet = new Set<THREE.Material>();
  nextRobot.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) {
      return;
    }

    child.castShadow = false;
    child.receiveShadow = false;

    const sourceMaterials = Array.isArray(child.material) ? child.material : [child.material];
    sourceMaterials.forEach((material) => sourceMaterialSet.add(material));
    const materials = sourceMaterials.map((material) => material.clone());
    child.material = Array.isArray(child.material) ? materials : materials[0];

    for (const material of materials) {
      if ('map' in material && material.map) {
        material.map.colorSpace = THREE.SRGBColorSpace;
      }
      if ('flatShading' in material) {
        material.flatShading = false;
      }
      // Open Cascade preserved the AP214 colors but emitted no physical PBR
      // factors, which glTF interprets as fully metallic. Without an HDR
      // environment that makes every colored part unnaturally dark. Use a
      // neutral semi-matte CAD preview surface while retaining its exact color.
      if ('metalness' in material) {
        material.metalness = Math.min(material.metalness, 0.18);
      }
      if ('roughness' in material) {
        material.roughness = Math.max(material.roughness, 0.58);
      }
      if ('emissive' in material) {
        material.emissive.setHex(0x000000);
        material.emissiveIntensity = 0;
      }
      material.needsUpdate = true;
    }
  });

  // Every displayed mesh owns a clone so later display changes cannot leak
  // through a material shared by multiple CAD instances. The source glTF/STL
  // materials are no longer referenced after this traversal.
  sourceMaterialSet.forEach((material) => material.dispose());
}

function createUrdfLoader(manager: THREE.LoadingManager): URDFLoader {
  const loader = new URDFLoader(manager);
  loader.fetchOptions = { mode: 'cors', credentials: 'same-origin' };
  loader.workingPath = XACRO_WORKING_PATH;
  loader.packages = {
    'pql-a00_description': DESCRIPTION_PACKAGE_URL,
  };
  loader.parseCollision = false;
  return loader;
}

function parseRobotSkeleton(xml: XMLDocument): URDFRobot {
  const manager = new THREE.LoadingManager();
  const loader = createUrdfLoader(manager);
  // The colored CAD GLB supplies every visible mesh. Returning empty groups
  // keeps the URDF link/joint hierarchy without downloading the legacy STLs.
  loader.loadMeshCb = (_path, _manager, done) => done(new THREE.Group());
  return loader.parse(xml);
}

function parseLegacyRobot(xml: XMLDocument): Promise<URDFRobot> {
  return new Promise((resolve, reject) => {
    const manager = new THREE.LoadingManager();
    let parsedRobot: URDFRobot | null = null;
    manager.onLoad = () => {
      if (parsedRobot) {
        resolve(parsedRobot);
      }
    };
    manager.onError = (url) => reject(new Error(`3D asset load failed: ${url}`));
    const loader = createUrdfLoader(manager);
    parsedRobot = loader.parse(xml);
  });
}

function findNamedDescendant(root: THREE.Object3D, prefix: string): THREE.Object3D | null {
  let match: THREE.Object3D | null = null;
  root.traverse((child) => {
    if (!match && child.name.startsWith(prefix)) {
      match = child;
    }
  });
  return match;
}

function findNamedDescendants(root: THREE.Object3D, prefix: string): THREE.Object3D[] {
  const matches: THREE.Object3D[] = [];
  root.traverse((child) => {
    if (child.name.startsWith(prefix)) {
      matches.push(child);
    }
  });
  return matches;
}

function geometryCenter(root: THREE.Object3D, label: string): THREE.Vector3 {
  const bounds = new THREE.Box3().setFromObject(root);
  if (bounds.isEmpty()) {
    throw new Error(`Colored CAD ${label} has no geometry: ${root.name}`);
  }
  return bounds.getCenter(new THREE.Vector3());
}

function geometryWorldCorners(root: THREE.Object3D): THREE.Vector3[] {
  const corners: THREE.Vector3[] = [];
  root.updateWorldMatrix(true, true);
  root.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) {
      return;
    }
    if (!child.geometry.boundingBox) {
      child.geometry.computeBoundingBox();
    }
    const bounds = child.geometry.boundingBox;
    if (!bounds) {
      return;
    }
    for (const x of [bounds.min.x, bounds.max.x]) {
      for (const y of [bounds.min.y, bounds.max.y]) {
        for (const z of [bounds.min.z, bounds.max.z]) {
          corners.push(new THREE.Vector3(x, y, z).applyMatrix4(child.matrixWorld));
        }
      }
    }
  });
  return corners;
}

function objectQuaternionInRobotRoot(root: THREE.Object3D): THREE.Quaternion {
  robotRoot.updateWorldMatrix(true, false);
  root.updateWorldMatrix(true, false);
  const relative = new THREE.Matrix4()
    .copy(robotRoot.matrixWorld)
    .invert()
    .multiply(root.matrixWorld);
  const position = new THREE.Vector3();
  const quaternion = new THREE.Quaternion();
  const scale = new THREE.Vector3();
  relative.decompose(position, quaternion, scale);
  return quaternion;
}

function setJointWorldPivot(joint: URDFJoint, worldPivot: THREE.Vector3): void {
  if (!joint.parent) {
    throw new Error(`URDF joint has no parent: ${joint.name}`);
  }

  joint.parent.updateWorldMatrix(true, false);
  const localPivot = joint.parent.worldToLocal(worldPivot.clone());
  joint.position.copy(localPivot);

  // urdf-loader captures these values on the first setJointValue() call.
  // Keep its internal zero-pose origin consistent with the CAD-derived pivot.
  const reframedJoint = joint as URDFJoint & { origPosition: THREE.Vector3 | null };
  reframedJoint.origPosition = localPivot.clone();
  joint.updateMatrixWorld(true);
}

function createCylinderBinding(
  cylinderRoot: THREE.Object3D,
  proximalLink: THREE.Object3D,
  distalLink: THREE.Object3D,
  bodyMode: 'fixed' | 'pivot',
): CylinderBinding {
  const rodAssembly = findNamedDescendant(cylinderRoot, 's-23-44_rod_assy');
  const tube = findNamedDescendant(cylinderRoot, 's2-23-45_tube');
  const rodEnds = findNamedDescendants(cylinderRoot, 'M5rodend_body');
  if (!rodAssembly || !tube || rodEnds.length === 0) {
    throw new Error(`Cylinder parts are incomplete: ${cylinderRoot.name}`);
  }

  const rodCenter = geometryCenter(rodAssembly, `${cylinderRoot.name} rod`);
  const endCenters = rodEnds.map((end) => ({
    end,
    center: geometryCenter(end, `${cylinderRoot.name} rod end`),
  }));
  const moving = endCenters.reduce((closest, candidate) =>
    candidate.center.distanceToSquared(rodCenter) < closest.center.distanceToSquared(rodCenter)
      ? candidate
      : closest,
  );

  let fixedPosition: THREE.Vector3;
  if (endCenters.length > 1) {
    fixedPosition = endCenters
      .filter((candidate) => candidate.end !== moving.end)
      .reduce((farthest, candidate) =>
        candidate.center.distanceToSquared(moving.center) >
        farthest.center.distanceToSquared(moving.center)
          ? candidate
          : farthest,
      ).center;
  } else {
    // The hip cylinder has only one separate rod-end body. Its fixed clevis is
    // integrated into the cover, so find the farthest body-side point along
    // the line from the moving rod end through the tube.
    const bodyDirection = geometryCenter(tube, `${cylinderRoot.name} tube`)
      .sub(moving.center)
      .normalize();
    const corners = geometryWorldCorners(cylinderRoot);
    if (corners.length === 0 || bodyDirection.lengthSq() === 0) {
      throw new Error(`Cylinder axis could not be measured: ${cylinderRoot.name}`);
    }
    const fixedDistance = Math.max(
      ...corners.map((corner) => corner.clone().sub(moving.center).dot(bodyDirection)),
    );
    fixedPosition = moving.center.clone().addScaledVector(bodyDirection, fixedDistance);
  }

  const fixedRoot = robotRoot.worldToLocal(fixedPosition.clone());
  const movingRoot = robotRoot.worldToLocal(moving.center.clone());
  const zeroDirectionRoot = movingRoot.clone().sub(fixedRoot);
  const zeroLength = zeroDirectionRoot.length();
  if (zeroLength < 0.02) {
    throw new Error(`Cylinder anchor distance is too short: ${cylinderRoot.name}`);
  }
  zeroDirectionRoot.normalize();

  // Pivot the entire fixed cylinder body about its proximal attachment while
  // preserving the exact STEP zero pose.
  const bodyRig = new THREE.Group();
  bodyRig.name = `${cylinderRoot.name}_display_rig`;
  bodyRig.position.copy(fixedRoot);
  robotRoot.add(bodyRig);
  robotRoot.updateMatrixWorld(true);
  bodyRig.attach(cylinderRoot);
  proximalLink.attach(bodyRig);

  const movingAnchor = new THREE.Object3D();
  movingAnchor.name = `${cylinderRoot.name}_moving_anchor`;
  movingAnchor.position.copy(movingRoot);
  robotRoot.add(movingAnchor);
  robotRoot.updateMatrixWorld(true);
  distalLink.attach(movingAnchor);

  // The rod, piston and moving rod-end form one sliding group. They keep their
  // imported transforms; only this wrapper translates along the cylinder axis.
  const rodRig = new THREE.Group();
  rodRig.name = `${cylinderRoot.name}_rod_rig`;
  bodyRig.add(rodRig);
  bodyRig.updateMatrixWorld(true);
  rodRig.attach(rodAssembly);
  if (!moving.end.parent || moving.end.parent !== rodRig) {
    rodRig.attach(moving.end);
  }

  const zeroRigQuaternionRoot = objectQuaternionInRobotRoot(bodyRig);
  const rodAxisInRig = zeroDirectionRoot
    .clone()
    .applyQuaternion(zeroRigQuaternionRoot.clone().invert())
    .normalize();

  return {
    name: cylinderRoot.name,
    bodyMode,
    bodyRig,
    rodRig,
    movingAnchor,
    zeroDirectionRoot,
    zeroRigQuaternionRoot,
    zeroLength,
    zeroRodPosition: rodRig.position.clone(),
    rodAxisInRig,
  };
}

function updateCylinderBindings(): void {
  if (cylinderBindings.length === 0) {
    return;
  }

  robotRoot.updateMatrixWorld(true);
  cylinderRootInverse.copy(robotRoot.matrixWorld).invert();

  for (const binding of cylinderBindings) {
    const parent = binding.bodyRig.parent;
    if (!parent) {
      continue;
    }

    binding.bodyRig.getWorldPosition(cylinderPosition);
    binding.movingAnchor.getWorldPosition(cylinderMovingPosition);
    cylinderPosition.applyMatrix4(cylinderRootInverse);
    cylinderMovingPosition.applyMatrix4(cylinderRootInverse);
    cylinderDirection.copy(cylinderMovingPosition).sub(cylinderPosition);
    const anchorDistance = cylinderDirection.length();
    if (anchorDistance < 1e-5) {
      continue;
    }

    let currentLength: number;
    if (binding.bodyMode === 'pivot') {
      cylinderDirection.normalize();
      currentLength = anchorDistance;
      cylinderDeltaQuaternion.setFromUnitVectors(binding.zeroDirectionRoot, cylinderDirection);
      cylinderDesiredQuaternion
        .copy(cylinderDeltaQuaternion)
        .multiply(binding.zeroRigQuaternionRoot);
      cylinderDesiredInRoot.compose(
        cylinderPosition,
        cylinderDesiredQuaternion,
        cylinderUnitScale,
      );

      parent.updateWorldMatrix(true, false);
      cylinderParentInRoot
        .copy(cylinderRootInverse)
        .multiply(parent.matrixWorld);
      cylinderLocalMatrix
        .copy(cylinderParentInRoot)
        .invert()
        .multiply(cylinderDesiredInRoot)
        .decompose(binding.bodyRig.position, binding.bodyRig.quaternion, binding.bodyRig.scale);
    } else {
      // hip: the cylinder body is clamped to the proximal link. Keep its
      // imported position and orientation unchanged, and use only the moving
      // anchor's projection onto the fixed cylinder axis as rod stroke.
      cylinderFixedAxis
        .copy(binding.rodAxisInRig)
        .applyQuaternion(objectQuaternionInRobotRoot(binding.bodyRig))
        .normalize();
      currentLength = cylinderDirection.dot(cylinderFixedAxis);
    }

    binding.rodRig.position
      .copy(binding.zeroRodPosition)
      .addScaledVector(binding.rodAxisInRig, currentLength - binding.zeroLength);
    binding.bodyRig.updateMatrixWorld(true);
  }
}

function neutralizeRobotJoints(nextRobot: URDFRobot): void {
  for (const jointMap of Object.values(LEG_JOINTS)) {
    nextRobot.setJointValue(jointMap.fixed, 0);
    nextRobot.setJointValue(jointMap.hip, 0);
    nextRobot.setJointValue(jointMap.knee, 0);
  }
  nextRobot.updateMatrixWorld(true);
}

function classifyCadLeg(root: THREE.Object3D): LegId {
  const bounds = new THREE.Box3().setFromObject(root);
  if (bounds.isEmpty()) {
    throw new Error(`Colored CAD leg has no geometry: ${root.name}`);
  }
  const center = bounds.getCenter(new THREE.Vector3());
  robotRoot.worldToLocal(center);
  const longitudinal = center.y < 0 ? 'front' : 'rear';
  const lateral = center.x < 0 ? 'right' : 'left';
  return `${longitudinal}_${lateral}` as LegId;
}

function attachColoredCadModel(nextRobot: URDFRobot, cadScene: THREE.Group): void {
  cylinderBindings.length = 0;
  contactFootObjects.clear();
  neutralizeRobotJoints(nextRobot);
  robotRoot.add(nextRobot);

  // Open Cascade exports glTF in Y-up coordinates. The URDF/Three scene is
  // X-forward/Y-left/Z-up, so +90 degrees about X maps (x,y,z) -> (x,-z,y).
  const alignment = new THREE.Group();
  alignment.rotation.x = Math.PI / 2;
  alignment.add(cadScene);
  robotRoot.add(alignment);
  robotRoot.updateMatrixWorld(true);

  // GLTFLoader sanitizes spaces and punctuation in node names for animation
  // bindings ("PQL01 assy" becomes "PQL01_assy").
  const assemblyRoot = findNamedDescendant(cadScene, 'PQL01_assy');
  const baseLink = nextRobot.links.base_link;
  if (!assemblyRoot || !baseLink) {
    throw new Error('Colored CAD root or URDF base_link was not found');
  }

  const cadLegRoots = assemblyRoot.children.filter((child) => child.name.startsWith('PQL-LG00'));
  if (cadLegRoots.length !== 4) {
    throw new Error(`Expected four colored CAD leg roots, found ${cadLegRoots.length}`);
  }

  const byLeg = new Map<LegId, THREE.Object3D>();
  for (const cadLegRoot of cadLegRoots) {
    const legId = classifyCadLeg(cadLegRoot);
    if (byLeg.has(legId)) {
      throw new Error(`Duplicate colored CAD leg classification: ${legId}`);
    }
    byLeg.set(legId, cadLegRoot);
  }
  if (byLeg.size !== 4) {
    throw new Error(`Could not classify all colored CAD legs (classified ${byLeg.size})`);
  }

  // Attach the complete assembly to the body first, then move the articulated
  // leg subtrees to their URDF links. Object3D.attach() preserves their world
  // transforms, so the CAD home pose remains unchanged at zero joint angle.
  baseLink.attach(assemblyRoot);
  // This application intentionally renders one leg only. The body remains in
  // the loaded hierarchy for joint transforms, but its CAD geometry is hidden.
  assemblyRoot.visible = false;
  for (const [legId, cadLegRoot] of byLeg) {
    const jointMap = LEG_JOINTS[legId];
    const firstLink = nextRobot.links[jointMap.links[0]];
    const upperLink = nextRobot.links[jointMap.links[1]];
    const lowerLink = nextRobot.links[jointMap.links[2]];
    const upperCad = findNamedDescendant(cadLegRoot, 'PQL01-LU00-A1');
    const lowerCad = findNamedDescendant(cadLegRoot, 'PQL01-LD00-A1');
    const hipJoint = nextRobot.joints[jointMap.hip];
    const kneeJoint = nextRobot.joints[jointMap.knee];
    // GLTFLoader sanitizes spaces in node names for animation bindings.
    const hipShaft = upperCad ? findNamedDescendant(upperCad, 'Leg_r_shaft') : null;
    const kneeShaft = lowerCad ? findNamedDescendant(lowerCad, 'Leg_under_shaft') : null;
    if (
      !firstLink ||
      !upperLink ||
      !lowerLink ||
      !upperCad ||
      !lowerCad ||
      !hipJoint ||
      !kneeJoint ||
      !hipShaft ||
      !kneeShaft
    ) {
      throw new Error(`Colored CAD/URDF link mapping is incomplete for ${legId}`);
    }

    // The legacy URDF joint origins were measured for the old split STL model.
    // The complete STEP assembly uses a different home pose, so rotating the
    // new geometry around those origins makes the links orbit away from their
    // visible shafts. Derive each physical pivot from the shaft geometry while
    // it is still in the untouched CAD assembly, then reframe the empty URDF
    // skeleton before attaching the visible subassemblies.
    const hipPivot = geometryCenter(hipShaft, `${legId} hip shaft`);
    const kneePivot = geometryCenter(kneeShaft, `${legId} knee shaft`);
    setJointWorldPivot(hipJoint, hipPivot);
    nextRobot.updateMatrixWorld(true);
    setJointWorldPivot(kneeJoint, kneePivot);
    nextRobot.updateMatrixWorld(true);

    firstLink.attach(cadLegRoot);
    upperLink.attach(upperCad);
    lowerLink.attach(lowerCad);
    const isFocusedLeg = legId === props.focusedLegId;
    firstLink.visible = isFocusedLeg;
    const footCad = findNamedDescendant(lowerCad, 'leg_cap_gom3');
    if (footCad) {
      contactFootObjects.set(legId, footCad);
    }
    nextRobot.updateMatrixWorld(true);

    if (!isFocusedLeg) {
      continue;
    }

    const cylinderRoots = [...cadLegRoot.children].filter((child) =>
      child.name.startsWith('s2-23-45'),
    );
    if (cylinderRoots.length !== 2) {
      console.warn(`Expected two CAD cylinders for ${legId}, found ${cylinderRoots.length}`);
    }
    for (const cylinderRoot of cylinderRoots) {
      const isHipCylinder = cylinderRoot.name.startsWith('s2-23-45_2');
      const proximalLink = isHipCylinder ? firstLink : upperLink;
      const distalLink = isHipCylinder ? upperLink : lowerLink;
      try {
        cylinderBindings.push(
          createCylinderBinding(
            cylinderRoot,
            proximalLink,
            distalLink,
            isHipCylinder ? 'fixed' : 'pivot',
          ),
        );
      } catch (cylinderError) {
        // Cylinder animation is an optional visual enhancement. Keep the full
        // colored robot instead of falling back to legacy STLs if one binding
        // cannot be derived from a future CAD revision.
        console.warn(`CAD cylinder animation disabled for ${cylinderRoot.name}`, cylinderError);
      }
    }
  }

  robotRoot.remove(alignment);
  configureRobotMaterials(nextRobot);
}

function updateContactRipples(timestamp: number): boolean {
  if (!robot) {
    return false;
  }

  const supportingLegs = new Set(props.supportingLegIds ?? []);
  robotRoot.updateMatrixWorld(true);
  let active = false;

  for (const [legId, ripple] of contactRipples) {
    const supporting = supportingLegs.has(legId);
    ripple.group.visible = supporting;
    if (!supporting) {
      continue;
    }

    const lowerLink = robot.links[LEG_JOINTS[legId].links[2]];
    const foot = contactFootObjects.get(legId) ?? lowerLink;
    if (!foot) {
      ripple.group.visible = false;
      continue;
    }

    contactBounds.makeEmpty().setFromObject(foot);
    if (contactBounds.isEmpty()) {
      ripple.group.visible = false;
      continue;
    }
    contactBounds.getCenter(contactCenter);
    ripple.group.position.set(contactCenter.x, contactCenter.y, contactBounds.min.z - 0.0015);
    ripple.group.quaternion.identity();
    active = true;

    for (const [index, ring] of ripple.rings.entries()) {
      const phase = (timestamp / 1500 + index / ripple.rings.length) % 1;
      const scale = 0.65 + phase * 1.85;
      ring.scale.set(scale, scale, 1);
      ring.material.opacity = (1 - phase) * 0.48;
    }
  }

  return active;
}

function animateContactRipples(timestamp: number): void {
  rippleAnimationFrame = null;
  if (timestamp - rippleLastFrameAt < RIPPLE_FRAME_INTERVAL_MS) {
    rippleAnimationFrame = requestAnimationFrame(animateContactRipples);
    return;
  }
  rippleLastFrameAt = timestamp;
  const active = updateContactRipples(timestamp);
  renderScene();
  if (active) {
    rippleAnimationFrame = requestAnimationFrame(animateContactRipples);
  }
}

function refreshContactRipples(): void {
  const active = updateContactRipples(performance.now());
  if (active && rippleAnimationFrame === null) {
    rippleAnimationFrame = requestAnimationFrame(animateContactRipples);
  } else if (!active && rippleAnimationFrame !== null) {
    cancelAnimationFrame(rippleAnimationFrame);
    rippleAnimationFrame = null;
  }
}

function fitCameraToRobot(): void {
  if (!robot) {
    return;
  }

  robot.updateMatrixWorld(true);
  const focusedLink = robot.links[LEG_JOINTS[props.focusedLegId].links[0]];
  const bounds = new THREE.Box3().setFromObject(focusedLink ?? robot);
  if (bounds.isEmpty()) {
    return;
  }

  const center = bounds.getCenter(new THREE.Vector3());
  const size = bounds.getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const distance = Math.max(((maxDim * 0.5) / Math.tan(fov * 0.5)) * 1.05, 0.55);

  controls.target.copy(center);
  camera.position.set(
    center.x + distance * 0.72,
    center.y - distance * 0.92,
    center.z + distance * 0.58,
  );
  controls.minDistance = Math.max(distance * 0.45, 0.25);
  controls.maxDistance = distance * 3.5;
  camera.near = Math.max(distance / 100, 0.01);
  camera.far = distance * 10;
  camera.updateProjectionMatrix();
  controls.update();
}

function renderScene(): void {
  renderer.render(scene, camera);
}

const imuScratchQuaternion = new THREE.Quaternion();

// Reused only for the euler fallback path (no `imuQuaternion` payload available). Interprets
// roll_deg as a rotation about the IMU body's forward (X) axis and pitch_deg as a rotation
// about the body's left (Y) axis - the standard aerospace roll/pitch convention for the
// X-forward/Y-left/Z-up body frame documented in `utils/imuFrame.ts`. The previous
// implementation swapped these into `rotation.set(pitch, roll, 0, 'XYZ')`, which put pitch on
// the X axis and roll on the Y axis; that mismatch is corrected here alongside the frame fix.
const imuEulerScratch = new THREE.Euler(0, 0, 0, 'XYZ');

/**
 * Applies only the IMU-driven root orientation (`robotRoot.quaternion`). Joint poses and
 * cylinder linkage remain unchanged; the world-horizontal contact ripples are repositioned
 * after this update.
 */
function applyImuOrientation(): void {
  if (props.imuQuaternion) {
    imuScratchQuaternion.set(
      props.imuQuaternion.x,
      props.imuQuaternion.y,
      props.imuQuaternion.z,
      props.imuQuaternion.w,
    );
    robotRoot.quaternion.copy(imuQuaternionToScene(imuScratchQuaternion));
    return;
  }

  if (!props.imuOrientation) {
    robotRoot.quaternion.identity();
    return;
  }

  const roll = THREE.MathUtils.degToRad(props.imuOrientation.roll_deg);
  const pitch = THREE.MathUtils.degToRad(props.imuOrientation.pitch_deg);
  imuEulerScratch.set(roll, pitch, 0, 'XYZ');
  imuScratchQuaternion.setFromEuler(imuEulerScratch);
  robotRoot.quaternion.copy(imuQuaternionToScene(imuScratchQuaternion));
}

/** Full pose application: joints + cylinder linkage + IMU + contact ripples. */
function applyPose(): void {
  if (!robot) {
    return;
  }

  for (const leg of props.legs) {
    const jointMap = LEG_JOINTS[leg.leg_id];
    robot.setJointValue(jointMap.fixed, leg.fixed_joint_angle_rad);
    robot.setJointValue(jointMap.hip, leg.hip.angle_rad);
    robot.setJointValue(jointMap.knee, leg.knee.angle_rad);
  }

  robotRoot.updateMatrixWorld(true);
  updateCylinderBindings();
  applyImuOrientation();
  refreshContactRipples();
  renderScene();
}

function resizeRenderer(): void {
  const host = stageRef.value;
  if (!host) return;
  const { clientWidth, clientHeight } = host;
  if (clientWidth === 0 || clientHeight === 0) return;
  renderer.setSize(clientWidth, clientHeight, false);
  camera.aspect = clientWidth / clientHeight;
  camera.updateProjectionMatrix();
  renderScene();
}

function disposeRobot(nextRobot: URDFRobot | null): void {
  if (!nextRobot) {
    return;
  }

  nextRobot.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) {
      return;
    }

    child.geometry.dispose();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    for (const material of materials) {
      material.dispose();
    }
  });

  if (nextRobot.parent) {
    nextRobot.parent.remove(nextRobot);
  }
}

async function loadRobotModel(): Promise<void> {
  loading.value = true;
  error.value = '';

  const xacroLoader = new XacroLoader();
  xacroLoader.fetchOptions = { credentials: 'same-origin' };
  xacroLoader.workingPath = XACRO_WORKING_PATH;
  xacroLoader.rospackCommands = {
    find: (packageName: string) => {
      if (packageName === 'pql-a00_description') {
        return DESCRIPTION_PACKAGE_URL.replace(/\/$/, '');
      }
      throw new Error(`Unknown ROS package: ${packageName}`);
    },
  };

  const xml = await new Promise<XMLDocument>((resolve, reject) => {
    xacroLoader.load(XACRO_URL, resolve, reject);
  });

  let nextRobot: URDFRobot;
  let coloredRobot: URDFRobot | null = null;
  let cadScene: THREE.Group | null = null;
  try {
    const cad = await new GLTFLoader().loadAsync(COLORED_MODEL_URL);
    cadScene = cad.scene;
    coloredRobot = parseRobotSkeleton(xml);
    attachColoredCadModel(coloredRobot, cadScene);
    nextRobot = coloredRobot;
  } catch (cadError) {
    cylinderBindings.length = 0;
    contactFootObjects.clear();
    disposeRobot(coloredRobot);
    cadScene?.parent?.removeFromParent();
    throw new Error(
      `1脚CADモデルを読み込めませんでした: ${cadError instanceof Error ? cadError.message : String(cadError)}`,
    );
  }

  disposeRobot(robot);
  robot = nextRobot;
  fitCameraToRobot();
  applyPose();
  loading.value = false;
  renderScene();
}

onMounted(async () => {
  if (!stageRef.value) {
    return;
  }

  stageRef.value.appendChild(renderer.domElement);
  resizeRenderer();

  resizeObserver = new ResizeObserver(() => resizeRenderer());
  resizeObserver.observe(stageRef.value);
  controls.addEventListener('change', renderScene);

  try {
    await loadRobotModel();
  } catch (nextError) {
    error.value = nextError instanceof Error ? nextError.message : '3D モデルの読み込みに失敗しました。';
    loading.value = false;
    renderScene();
  }
});

// Joint pose, focused-leg, and support changes use the full pose path. The focus change
// remains in this watcher because it is coupled to the selected leg's UI state.
watch([poseSignature, () => props.focusedLegId, supportSignature], () => {
  applyPose();
});

// IMU-only updates stay cheap: orient the root and reposition the small ripple groups.
watch(imuSignature, () => {
  if (!robot) {
    return;
  }
  applyImuOrientation();
  updateContactRipples(performance.now());
  renderScene();
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  if (rippleAnimationFrame !== null) {
    cancelAnimationFrame(rippleAnimationFrame);
    rippleAnimationFrame = null;
  }
  for (const ripple of contactRipples.values()) {
    for (const ring of ripple.rings) {
      ring.geometry.dispose();
      ring.material.dispose();
    }
    scene.remove(ripple.group);
  }
  contactRipples.clear();
  contactFootObjects.clear();
  cylinderBindings.length = 0;
  controls.removeEventListener('change', renderScene);
  controls.dispose();
  disposeRobot(robot);
  robot = null;
  renderer.dispose();
  if (renderer.domElement.parentElement) {
    renderer.domElement.parentElement.removeChild(renderer.domElement);
  }
});
</script>

<template>
  <div class="robot-stage">
    <div ref="stageRef" class="robot-stage-canvas"></div>
    <div v-if="loading" class="robot-stage-overlay">3D モデルを読み込み中...</div>
    <div v-else-if="error" class="robot-stage-overlay is-error">{{ error }}</div>
  </div>
</template>
