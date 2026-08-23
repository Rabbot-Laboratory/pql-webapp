export type ControlMode = 'position' | 'command';
export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'emulated';
export type LegId = 'front_right';
export type TelemetryRecordingScope = 'all' | 'selected';

export interface ActuatorTelemetry {
  position: number;
  voltage: number;
  command: number;
  pressure: number;
}

export interface GainValues {
  p: number | null;
  i: number | null;
  d: number | null;
}

export interface CaptureValues {
  min: number | null;
  max: number | null;
}

export interface ActuatorState {
  actuator_id: number;
  label: 'hip' | 'knee';
  port_role: string;
  local_index: number;
  telemetry: ActuatorTelemetry;
  target_position: number;
  target_command: number;
  gains: GainValues;
  capture: CaptureValues;
  updated_at: string;
}

export interface SystemStatus {
  server_ok: boolean;
  connection_state: ConnectionState;
  emulate_devices: boolean;
  esp32_path: string;
  updated_at: string;
}

export interface TelemetrySample {
  timestamp: string;
  position: number;
  voltage: number;
  command: number;
  pressure: number;
  target_position: number;
  target_command: number;
}

export interface JointPreview {
  actuator_id: number;
  label: string;
  joint_name: string;
  position: number;
  angle_rad: number;
  target_position: number;
  target_angle_rad: number;
  command: number;
}

export interface LegPreview {
  leg_id: LegId;
  label: string;
  fixed_joint_name: string;
  fixed_joint_angle_rad: number;
  mirror_x: boolean;
  hip: JointPreview;
  knee: JointPreview;
  updated_at: string;
}

export interface ImuOrientation {
  roll_deg: number;
  pitch_deg: number;
  yaw_deg: number | null;
}

export interface ImuQuaternion {
  w: number;
  x: number;
  y: number;
  z: number;
}

export interface TelemetryEvent {
  type: string;
  timestamp?: string;
  payload: Record<string, unknown>;
}

