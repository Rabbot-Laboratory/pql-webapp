import type { AdcChannelState, LegId, SensorState } from '@/types/control';

export type ContactPolarity = 'active_high' | 'active_low';

export interface ContactChannelReading {
  channel: number;
  raw: number | null;
  voltage: number | null;
}

export interface ContactLegState {
  legId: LegId;
  channels: number[];
  readings: ContactChannelReading[];
  signalRaw: number | null;
  supporting: boolean;
}

// Display-only provisional wiring map. Verify every channel on the real robot
// before this signal is used by gait or stabilization control.
export const CONTACT_CHANNELS_BY_LEG: Record<LegId, number[]> = {
  front_right: [0],
  front_left: [1],
  rear_right: [2],
  rear_left: [3],
};

const LEG_ORDER: LegId[] = ['front_right', 'front_left', 'rear_right', 'rear_left'];

function readingForChannel(channels: AdcChannelState[], channel: number): ContactChannelReading {
  const reading = channels.find((item) => item.channel === channel);
  return {
    channel,
    raw: reading?.raw ?? null,
    voltage: reading?.voltage ?? null,
  };
}

export function deriveContactLegStates(
  sensors: SensorState | null,
  threshold: number,
  polarity: ContactPolarity,
): ContactLegState[] {
  const bank = sensors?.adc_banks.find((item) => item.device === 0) ?? sensors?.adc_banks[0];
  const bankConnected = bank?.connection_state === 'connected';
  const channels = bankConnected ? bank.channels : [];

  return LEG_ORDER.map((legId) => {
    const mappedChannels = CONTACT_CHANNELS_BY_LEG[legId];
    const readings = mappedChannels.map((channel) => readingForChannel(channels, channel));
    const values = readings.flatMap((reading) => (reading.raw === null ? [] : [reading.raw]));
    const signalRaw = values.length
      ? polarity === 'active_high'
        ? Math.max(...values)
        : Math.min(...values)
      : null;
    const supporting = values.some((value) =>
      polarity === 'active_high' ? value >= threshold : value <= threshold,
    );

    return {
      legId,
      channels: [...mappedChannels],
      readings,
      signalRaw,
      supporting,
    };
  });
}
