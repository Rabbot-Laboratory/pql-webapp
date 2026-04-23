<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import URDFLoader, { type URDFRobot } from 'urdf-loader';
import { XacroLoader } from 'xacro-parser';

import type { LegId, LegPreview } from '@/types/control';

const props = defineProps<{
  legs: LegPreview[];
  focusedLegId: LegId;
}>();

const DESCRIPTION_PACKAGE_URL = '/robot-description/pql-a00/';
const XACRO_URL = `${DESCRIPTION_PACKAGE_URL}urdf/pql-a00.xacro`;
const XACRO_WORKING_PATH = `${DESCRIPTION_PACKAGE_URL}urdf/`;

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

const scene = new THREE.Scene();
scene.background = new THREE.Color(0xf2f4f7);
const robotRoot = new THREE.Group();
scene.add(robotRoot);

const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 10);
camera.up.set(0, 0, 1);
camera.position.set(0.7, -0.95, 0.55);

const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
renderer.setClearColor(0xf2f4f7, 1);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.25));
renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.shadowMap.enabled = false;

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = false;
controls.target.set(0, 0, 0);
controls.minDistance = 0.3;
controls.maxDistance = 2.6;

const ambientLight = new THREE.HemisphereLight('#ffffff', '#d7dbe2', 1.35);
scene.add(ambientLight);

const keyLight = new THREE.DirectionalLight('#ffffff', 1.25);
keyLight.position.set(1.3, -1.1, 1.7);
scene.add(keyLight);

const fillLight = new THREE.DirectionalLight('#cfd8e3', 0.7);
fillLight.position.set(-0.9, 1.0, 0.9);
scene.add(fillLight);

let resizeObserver: ResizeObserver | null = null;
let robot: URDFRobot | null = null;

const poseSignature = computed(() =>
  props.legs
    .map(
      (leg) =>
        `${leg.leg_id}:${leg.fixed_joint_angle_rad.toFixed(4)}:${leg.hip.angle_rad.toFixed(4)}:${leg.knee.angle_rad.toFixed(4)}`,
    )
    .join('|'),
);

function setMeshEmissive(mesh: THREE.Mesh, color: number, intensity: number): void {
  const materials = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
  for (const material of materials) {
    if ('emissive' in material) {
      material.emissive.setHex(color);
      material.emissiveIntensity = intensity;
    }
  }
}

function configureRobotMaterials(nextRobot: URDFRobot): void {
  nextRobot.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) {
      return;
    }

    child.castShadow = false;
    child.receiveShadow = false;

    const sourceMaterials = Array.isArray(child.material) ? child.material : [child.material];
    const materials = sourceMaterials.map((material) => material.clone());
    child.material = Array.isArray(child.material) ? materials : materials[0];

    for (const material of materials) {
      if ('map' in material && material.map) {
        material.map.colorSpace = THREE.SRGBColorSpace;
      }
      if ('flatShading' in material) {
        material.flatShading = false;
      }
      material.needsUpdate = true;
    }
  });
}

function applyFocusedHighlight(): void {
  if (!robot) {
    return;
  }

  robot.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      setMeshEmissive(child, 0x000000, 0);
    }
  });

  const focused = LEG_JOINTS[props.focusedLegId];
  for (const linkName of focused.links) {
    const link = robot.links[linkName];
    if (!link) {
      continue;
    }

    link.traverse((child) => {
      if (child instanceof THREE.Mesh) {
        setMeshEmissive(child, 0x22313f, 0.03);
      }
    });
  }
}

function fitCameraToRobot(): void {
  if (!robot) {
    return;
  }

  robot.updateMatrixWorld(true);
  const bounds = new THREE.Box3().setFromObject(robot);
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

  applyFocusedHighlight();
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

  const manager = new THREE.LoadingManager();
  manager.onLoad = () => {
    if (robot) {
      configureRobotMaterials(robot);
    }
    fitCameraToRobot();
    applyPose();
    loading.value = false;
    renderScene();
  };
  manager.onError = (url) => {
    error.value = `3D アセットの読み込みに失敗しました: ${url}`;
    loading.value = false;
    renderScene();
  };

  const loader = new URDFLoader(manager);
  loader.fetchOptions = { mode: 'cors', credentials: 'same-origin' };
  loader.workingPath = XACRO_WORKING_PATH;
  loader.packages = {
    'pql-a00_description': DESCRIPTION_PACKAGE_URL,
  };
  loader.parseCollision = false;

  const nextRobot = loader.parse(xml);

  disposeRobot(robot);
  robot = nextRobot;
  robotRoot.add(nextRobot);
  applyPose();
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

watch([poseSignature, () => props.focusedLegId], () => {
  applyPose();
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
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
