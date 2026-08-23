from __future__ import annotations

from highend_server.application.attitude_control_frame import AttitudeControlFrame


def test_20260711_trial_signs_map_raw_attitude_to_control_attitude() -> None:
    frame = AttitudeControlFrame()

    # Manual trial: right-side-down was negative raw Roll, while nose-up was
    # positive raw Pitch.  Both must be positive in the controller convention.
    roll, pitch = frame.tilt(
        raw_roll_deg=-12.0,
        raw_pitch_deg=8.0,
        level_roll_offset_deg=-2.0,
        level_pitch_offset_deg=1.0,
    )

    assert roll == 10.0
    assert pitch == 7.0


def test_control_error_rates_apply_the_same_trial_signs() -> None:
    frame = AttitudeControlFrame()
    # error = setpoint - control attitude, so d(error)/dt is gyro_x for the
    # trial-derived Roll inversion and -gyro_y for unchanged Pitch.
    assert frame.error_rates(gyro_x_dps=3.0, gyro_y_dps=-4.0) == (3.0, 4.0)
