import type { ConnectionState, ControlMode, LegId, PlaybackStatus } from '@/types/control';

export function connectionStateLabel(state: ConnectionState | null | undefined): string {
  switch (state) {
    case 'connected':
      return '接続済み';
    case 'connecting':
      return '接続中';
    case 'error':
      return 'エラー';
    case 'disconnected':
      return '未接続';
    default:
      return '不明';
  }
}

export function playbackStatusLabel(state: PlaybackStatus | null | undefined): string {
  switch (state) {
    case 'idle':
      return '停止中';
    case 'running':
      return '再生中';
    case 'stopping':
      return '停止処理中';
    default:
      return '不明';
  }
}

export function wsStateLabel(state: 'connecting' | 'live' | 'disconnected' | 'error'): string {
  switch (state) {
    case 'live':
      return '接続中';
    case 'connecting':
      return '接続準備中';
    case 'disconnected':
      return '切断';
    case 'error':
      return 'エラー';
  }
}

export function controlModeLabel(mode: ControlMode): string {
  return mode === 'command' ? 'Command' : 'Position';
}

export function emulationLabel(emulateDevices: boolean | null | undefined): string {
  return emulateDevices ? 'デモモード' : '実機接続';
}

export function eventTypeLabel(type: string): string {
  const mapping: Record<string, string> = {
    snapshot: 'スナップショット',
    status: '状態',
    telemetry: 'テレメトリ',
    actuator_state: 'アクチュエータ',
    gain_response: 'ゲイン',
    preview: '3D表示',
    ws: '接続',
    command: '指令',
    server_status: '状態',
    leg_preview: '3D表示',
    csv_playback_status: '再生',
  };
  return mapping[type] ?? type;
}

export function legLabel(legId: LegId | string): string {
  const mapping: Record<string, string> = {
    front_right: '右前脚',
    front_left: '左前脚',
    rear_right: '右後脚',
    rear_left: '左後脚',
  };
  return mapping[legId] ?? legId;
}

export function portRoleLabel(portRole: string): string {
  const mapping: Record<string, string> = {
    Front: '前側基板',
    Back: '後側基板',
  };
  return mapping[portRole] ?? portRole;
}

export function actuatorLabel(label: string): string {
  const mapping: Record<string, string> = {
    'front right hip': '右前 hip',
    'front right knee': '右前 knee',
    'front left hip': '左前 hip',
    'front left knee': '左前 knee',
    'rear right hip': '右後 hip',
    'rear right knee': '右後 knee',
    'rear left hip': '左後 hip',
    'rear left knee': '左後 knee',
  };
  return mapping[label] ?? label;
}
