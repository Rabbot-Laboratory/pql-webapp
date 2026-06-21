import type { LegId } from '@/types/control';

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
