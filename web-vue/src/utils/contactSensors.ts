import type {
  ContactCalibration,
  LegId,
  SensorState,
  ServerContactLegState,
} from '@/types/control';

export type { ContactPolarity } from '@/types/control';

/** Panel-facing view of one leg's server-side contact judgement. */
export interface ContactLegState {
  legId: LegId;
  channel: number | null;
  raw: number | null;
  voltage: number | null;
  supporting: boolean;
}

const LEG_ORDER: LegId[] = ['front_right', 'front_left', 'rear_right', 'rear_left'];

/**
 * Contact detection now lives server-side (hysteresis + debounce in
 * highend_server.sensors.contact); this only reshapes SensorState.contact
 * for display, annotated with the calibrated channel mapping.
 */
export function deriveContactLegStates(
  sensors: SensorState | null,
  calibration: ContactCalibration | null,
): ContactLegState[] {
  const byLeg = new Map<LegId, ServerContactLegState>(
    (sensors?.contact ?? []).map((state) => [state.leg, state]),
  );
  const channelByLeg = new Map<LegId, number>(
    (calibration?.legs ?? []).map((leg) => [leg.leg, leg.channel]),
  );
  return LEG_ORDER.map((legId) => {
    const state = byLeg.get(legId);
    return {
      legId,
      channel: channelByLeg.get(legId) ?? null,
      raw: state?.raw ?? null,
      voltage: state?.voltage ?? null,
      supporting: state?.supporting ?? false,
    };
  });
}
