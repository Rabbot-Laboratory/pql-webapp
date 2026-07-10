<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';

import type { ImuOrientation, ImuQuaternion, SensorConnectionState } from '@/types/control';
import { imuQuaternionToScene, Q_T } from '@/utils/imuFrame';

const props = defineProps<{
  quaternion: ImuQuaternion | null;
  orientation: ImuOrientation | null;
  connectionState: SensorConnectionState;
}>();

const stageRef = ref<HTMLDivElement | null>(null);
const isConnected = computed(() => props.connectionState === 'connected');

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x10151d);

const camera = new THREE.PerspectiveCamera(36, 1, 0.01, 20);
camera.up.set(0, 0, 1);
camera.position.set(3.2, -4.2, 2.8);

const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setClearColor(0x10151d, 1);
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
renderer.outputColorSpace = THREE.SRGBColorSpace;

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.enablePan = false;
controls.target.set(0, 0, 0.25);
controls.minDistance = 2.5;
controls.maxDistance = 8;

scene.add(new THREE.HemisphereLight(0xffffff, 0x233044, 2.1));
const keyLight = new THREE.DirectionalLight(0xffffff, 2.4);
keyLight.position.set(3, -4, 6);
scene.add(keyLight);

const grid = new THREE.GridHelper(7, 14, 0x64748b, 0x263241);
grid.rotation.x = Math.PI / 2;
grid.position.z = -0.55;
scene.add(grid);

// The attitude group is expressed in the three.js scene frame. The child group applies
// the fixed IMU-body -> scene basis mapping so the arrows remain the sensor's own X/Y/Z axes.
const attitudeGroup = new THREE.Group();
const sensorBody = new THREE.Group();
sensorBody.quaternion.copy(Q_T);
attitudeGroup.add(sensorBody);
scene.add(attitudeGroup);

const boardMaterial = new THREE.MeshStandardMaterial({
  color: 0x176b55,
  roughness: 0.55,
  metalness: 0.15,
});
const boardGeometry = new THREE.BoxGeometry(2.15, 1.35, 0.16);
const board = new THREE.Mesh(boardGeometry, boardMaterial);
sensorBody.add(board);

const chipMaterial = new THREE.MeshStandardMaterial({
  color: 0x111827,
  roughness: 0.42,
  metalness: 0.25,
});
const chipGeometry = new THREE.BoxGeometry(0.62, 0.62, 0.22);
const chip = new THREE.Mesh(chipGeometry, chipMaterial);
chip.position.z = 0.18;
sensorBody.add(chip);

const markerMaterial = new THREE.MeshBasicMaterial({ color: 0xe2e8f0 });
const markerGeometry = new THREE.CircleGeometry(0.1, 24);
for (const [x, y] of [
  [-0.86, -0.46],
  [-0.86, 0.46],
  [0.86, -0.46],
  [0.86, 0.46],
] as const) {
  const marker = new THREE.Mesh(markerGeometry, markerMaterial);
  marker.position.set(x, y, 0.086);
  sensorBody.add(marker);
}

function createAxisLabel(text: string, color: string, position: THREE.Vector3): THREE.Sprite {
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 128;
  const context = canvas.getContext('2d');
  if (context) {
    context.font = '700 72px sans-serif';
    context.textAlign = 'center';
    context.textBaseline = 'middle';
    context.fillStyle = color;
    context.shadowColor = '#000000';
    context.shadowBlur = 8;
    context.fillText(text, 64, 66);
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false });
  const sprite = new THREE.Sprite(material);
  sprite.position.copy(position);
  sprite.scale.setScalar(0.42);
  return sprite;
}

const axisLength = 1.72;
const axisOrigin = new THREE.Vector3(0, 0, 0.23);
const axes = [
  { label: 'X', direction: new THREE.Vector3(1, 0, 0), color: 0xef4444, cssColor: '#ff6262' },
  { label: 'Y', direction: new THREE.Vector3(0, 1, 0), color: 0x22c55e, cssColor: '#4ade80' },
  { label: 'Z', direction: new THREE.Vector3(0, 0, 1), color: 0x3b82f6, cssColor: '#60a5fa' },
];

for (const axis of axes) {
  const arrow = new THREE.ArrowHelper(axis.direction, axisOrigin, axisLength, axis.color, 0.32, 0.18);
  sensorBody.add(arrow);
  const labelPosition = axisOrigin.clone().add(axis.direction.clone().multiplyScalar(axisLength + 0.25));
  sensorBody.add(createAxisLabel(axis.label, axis.cssColor, labelPosition));
}

const quaternionScratch = new THREE.Quaternion();
const eulerScratch = new THREE.Euler(0, 0, 0, 'XYZ');

const poseSignature = computed(() => {
  if (props.quaternion) {
    return `${props.quaternion.w}:${props.quaternion.x}:${props.quaternion.y}:${props.quaternion.z}`;
  }
  if (props.orientation) {
    return `${props.orientation.roll_deg}:${props.orientation.pitch_deg}:${props.orientation.yaw_deg ?? 0}`;
  }
  return 'none';
});

function applyOrientation(): void {
  if (props.quaternion) {
    quaternionScratch
      .set(props.quaternion.x, props.quaternion.y, props.quaternion.z, props.quaternion.w)
      .normalize();
    attitudeGroup.quaternion.copy(imuQuaternionToScene(quaternionScratch));
  } else if (props.orientation) {
    eulerScratch.set(
      THREE.MathUtils.degToRad(props.orientation.roll_deg),
      THREE.MathUtils.degToRad(props.orientation.pitch_deg),
      THREE.MathUtils.degToRad(props.orientation.yaw_deg ?? 0),
      'XYZ',
    );
    quaternionScratch.setFromEuler(eulerScratch);
    attitudeGroup.quaternion.copy(imuQuaternionToScene(quaternionScratch));
  } else {
    attitudeGroup.quaternion.identity();
  }
}

function resizeRenderer(): void {
  const host = stageRef.value;
  if (!host || host.clientWidth === 0 || host.clientHeight === 0) {
    return;
  }
  renderer.setSize(host.clientWidth, host.clientHeight, false);
  camera.aspect = host.clientWidth / host.clientHeight;
  camera.updateProjectionMatrix();
}

let resizeObserver: ResizeObserver | null = null;
let animationFrame = 0;

function animate(): void {
  animationFrame = requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

onMounted(() => {
  const host = stageRef.value;
  if (!host) {
    return;
  }
  host.appendChild(renderer.domElement);
  resizeRenderer();
  resizeObserver = new ResizeObserver(resizeRenderer);
  resizeObserver.observe(host);
  applyOrientation();
  animate();
});

watch(poseSignature, applyOrientation);

onBeforeUnmount(() => {
  cancelAnimationFrame(animationFrame);
  resizeObserver?.disconnect();
  controls.dispose();
  scene.traverse((object) => {
    if (object instanceof THREE.Mesh || object instanceof THREE.Line) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach((material) => material.dispose());
    } else if (object instanceof THREE.Sprite) {
      object.material.map?.dispose();
      object.material.dispose();
    }
  });
  renderer.dispose();
  renderer.domElement.remove();
});
</script>

<template>
  <div class="imu-orientation-view">
    <div class="imu-orientation-view__header">
      <div>
        <strong>IMU 3D姿勢</strong>
        <span>ドラッグ: 回転 / ホイール: 拡大縮小</span>
      </div>
      <div class="imu-axis-legend" aria-label="IMU軸の色">
        <span class="is-x">X</span>
        <span class="is-y">Y</span>
        <span class="is-z">Z</span>
      </div>
    </div>
    <div ref="stageRef" class="imu-orientation-view__canvas"></div>
    <div v-if="!isConnected" class="imu-orientation-view__overlay">
      IMU接続待ち
    </div>
  </div>
</template>
