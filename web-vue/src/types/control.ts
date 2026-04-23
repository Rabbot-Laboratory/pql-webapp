export type ConnectionState = 'disconnected' | 'connecting' | 'connected' | 'error';
export type PlaybackStatus = 'idle' | 'running' | 'stopping';
export type ControlMode = 'position' | 'command';
export type LegId = 'front_right' | 'front_left' | 'rear_right' | 'rear_left';
export type FixedMotion = 'crawl' | 'trot' | 'pace' | 'bound';
export type MotionCategory = 'fixed' | 'custom';
export type PlaybackAdvanceMode = 'time' | 'guarded';
export type TelemetryRecordingScope = 'all' | 'selected';

export interface SystemStatus {
  connection_state: ConnectionState;
  playback_status: PlaybackStatus;
  current_motion_name: string | null;
  current_motion_category: MotionCategory | null;
  current_motion_loop: boolean;
  emulate_devices: boolean;
  telemetry_recording: boolean;
  telemetry_log_name: string | null;
  telemetry_recording_scope: TelemetryRecordingScope;
  telemetry_recording_actuator_id: number | null;
  updated_at: string;
}

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
  label: string;
  port_role: string;
  local_index: number;
  telemetry: ActuatorTelemetry;
  target_position: number;
  target_command: number;
  gains: GainValues;
  capture: CaptureValues;
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

export interface PreviewCalibration {
  hip_offset_deg: number;
  knee_offset_deg: number;
}

export interface HealthResponse {
  ok: boolean;
  service: string;
  system: SystemStatus;
}

export interface MotionLibraryItem {
  name: string;
  category: MotionCategory;
  file_name: string;
  frame_count: number;
  axis_count: number;
  interval_sec: number | null;
  loop: boolean;
  advance_mode: PlaybackAdvanceMode;
  position_tolerance: number;
  pressure_threshold: number;
  step_timeout_sec: number;
  settle_time_sec: number;
  updated_at: string;
}

export interface MotionLibrarySnapshot {
  fixed: MotionLibraryItem[];
  custom: MotionLibraryItem[];
}

export interface MotionFileDetail {
  item: MotionLibraryItem;
  rows: string[][];
}

export interface ImportedMotionDraft {
  suggested_name: string;
  rows: string[][];
  frame_count: number;
  axis_count: number;
  interval_sec: number;
  loop: boolean;
  advance_mode: PlaybackAdvanceMode;
  position_tolerance: number;
  pressure_threshold: number;
  step_timeout_sec: number;
  settle_time_sec: number;
  source_format: string;
}

export interface TelemetryRecordingStatus {
  is_recording: boolean;
  current_log_name: string | null;
  latest_log_name: string | null;
  started_at: string | null;
  sample_count: number;
  scope: TelemetryRecordingScope;
  actuator_id: number | null;
}

export interface UiEventItem {
  id: string;
  type: string;
  message: string;
  timestamp: string;
}

export interface TelemetryEvent {
  type: string;
  timestamp: string;
  payload: Record<string, unknown>;
}
