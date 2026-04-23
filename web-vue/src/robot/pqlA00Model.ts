import * as THREE from 'three';

import type { LegId } from '@/types/control';

export interface AxisDefinition {
  x: number;
  y: number;
  z: number;
}

export interface LinkMeshDefinition {
  file: string;
  origin: [number, number, number];
}

export interface LegModelDefinition {
  legId: LegId;
  label: string;
  joint1Name: string;
  joint1Origin: [number, number, number];
  joint1Axis: AxisDefinition;
  rootLink: LinkMeshDefinition;
  joint2Name: string;
  joint2Origin: [number, number, number];
  joint2Axis: AxisDefinition;
  upperLink: LinkMeshDefinition;
  joint3Name: string;
  joint3Origin: [number, number, number];
  joint3Axis: AxisDefinition;
  lowerLink: LinkMeshDefinition;
}

export const ROBOT_SCALE = 0.001;
export const ROBOT_MESH_BASE_URL = '/robot-assets/pql-a00/meshes';

export const BODY_MESH: LinkMeshDefinition = {
  file: 'base_link.stl',
  origin: [0, 0, 0],
};

export const LEG_DEFINITIONS: Record<LegId, LegModelDefinition> = {
  front_right: {
    legId: 'front_right',
    label: 'Front Right',
    joint1Name: 'rev_fr1',
    joint1Origin: [-0.1308, -0.168616, 0.128684],
    joint1Axis: { x: 0, y: 1, z: 0 },
    rootLink: {
      file: 'PQL-LF00-FR_v2_1.stl',
      origin: [0.1308, 0.168616, -0.128684],
    },
    joint2Name: 'rev_fr2',
    joint2Origin: [-0.067, -0.1, -0.017],
    joint2Axis: { x: 1, y: 0, z: 0 },
    upperLink: {
      file: 'PQL01-LU00-A1-FR_v1_1.stl',
      origin: [0.1978, 0.268616, -0.111684],
    },
    joint3Name: 'rev_fr3',
    joint3Origin: [0.007, 0.16027, -0.19834],
    joint3Axis: { x: 1, y: 0, z: 0 },
    lowerLink: {
      file: 'PQL-LD00-FR_v1_1.stl',
      origin: [0.1908, 0.108346, 0.086656],
    },
  },
  front_left: {
    legId: 'front_left',
    label: 'Front Left',
    joint1Name: 'rev_fl1',
    joint1Origin: [0.1308, -0.134616, 0.128684],
    joint1Axis: { x: 0, y: -1, z: 0 },
    rootLink: {
      file: 'PQL-LF00-FL_v2_2.stl',
      origin: [-0.1308, 0.134616, -0.128684],
    },
    joint2Name: 'rev_fl2',
    joint2Origin: [0.066974, -0.134, -0.017103],
    joint2Axis: { x: 0.999999, y: 0, z: -0.001546 },
    upperLink: {
      file: 'PQL01-LU00-A1-FL_v1_1.stl',
      origin: [-0.197774, 0.268616, -0.111581],
    },
    joint3Name: 'rev_fl3',
    joint3Origin: [-0.007322, 0.147888, -0.207725],
    joint3Axis: { x: 0.999999, y: 0, z: -0.001546 },
    lowerLink: {
      file: 'PQL-LD00-FL_v1_1.stl',
      origin: [-0.190452, 0.120728, 0.096144],
    },
  },
  rear_right: {
    legId: 'rear_right',
    label: 'Rear Right',
    joint1Name: 'rev_rr1',
    joint1Origin: [-0.1233, 0.121384, 0.128684],
    joint1Axis: { x: 0, y: 1, z: 0 },
    rootLink: {
      file: 'PQL-LF00-FL_v2_1.stl',
      origin: [0.1233, -0.121384, -0.128684],
    },
    joint2Name: 'rev_rr2',
    joint2Origin: [-0.067042, 0.134001, -0.016833],
    joint2Axis: { x: -0.999997, y: 0, z: 0.002481 },
    upperLink: {
      file: 'PQL01-LU00-A1-RR_v1_2.stl',
      origin: [0.190342, -0.255385, -0.111851],
    },
    joint3Name: 'rev_rr3',
    joint3Origin: [0.026452, -0.1275, -0.220903],
    joint3Axis: { x: -0.999997, y: 0, z: 0.002481 },
    lowerLink: {
      file: 'PQL-LD00-RR_v1_1.stl',
      origin: [0.16389, -0.127885, 0.109052],
    },
  },
  rear_left: {
    legId: 'rear_left',
    label: 'Rear Left',
    joint1Name: 'rev_rl1',
    joint1Origin: [0.1308, 0.155384, 0.128684],
    joint1Axis: { x: 0, y: -1, z: 0 },
    rootLink: {
      file: 'PQL-LF00-RL_v1_1.stl',
      origin: [-0.1308, -0.155384, -0.128684],
    },
    joint2Name: 'rev_rl2',
    joint2Origin: [0.067, 0.100001, -0.017],
    joint2Axis: { x: -1, y: 0, z: 0 },
    upperLink: {
      file: 'PQL01-LU00-A1-RL_v1_1.stl',
      origin: [-0.1978, -0.255385, -0.111684],
    },
    joint3Name: 'rev_rl3',
    joint3Origin: [-0.007, -0.180313, -0.180312],
    joint3Axis: { x: -1, y: 0, z: 0 },
    lowerLink: {
      file: 'PQL-LD00-RL_v1_1.stl',
      origin: [-0.1908, -0.075072, 0.068628],
    },
  },
};

export function axisVector(axis: AxisDefinition): THREE.Vector3 {
  return new THREE.Vector3(axis.x, axis.y, axis.z).normalize();
}

