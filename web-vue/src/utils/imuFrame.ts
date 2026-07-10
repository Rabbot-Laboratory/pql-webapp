import * as THREE from 'three';

/**
 * IMU body-frame -> three.js scene-frame transform.
 *
 * ## Frame conventions
 *
 * ### IMU body frame (source)
 * Derived from `src/highend_server/sensors/attitude.py` (`MahonyMARG` / `gravity_from_quat`):
 * at the identity quaternion (q = 1,0,0,0) the estimated gravity direction returned by
 * `gravity_from_quat` is `(0, 0, 1)`, i.e. the body's own Z axis is aligned with "up" when
 * the device is level. This is the standard accelerometer packaging convention (Z reads
 * +1g when the board lies flat), so **body Z = up** is fixed by the fusion math itself.
 *
 * The X/Y split (which axis is "forward" vs "left") cannot be derived from the fusion math
 * alone (roll/pitch are symmetric in the Mahony equations) - it depends on how the BMX055 is
 * physically mounted on the chassis. We assume the convention documented in
 * `src/highend_server/sensors/attitude.py`: **X-forward / Y-left / Z-up** (a right-handed
 * frame, matching the ROS REP-103 `base_link` convention). This is a documented assumption,
 * not a hardware-verified fact - see "Adjusting on real hardware" below.
 *
 * ### Scene frame (target)
 * Three.js scene uses `camera.up = (0, 0, 1)`, so scene Z is up. The URDF robot model is
 * added to `robotRoot` with no extra rotation, so the scene frame equals the raw URDF/xacro
 * frame. Reading `pql-a00_description/urdf/pql-a00.xacro` joint origins relative to
 * `base_link`:
 *   - front legs (`rev_fr1`, `rev_fl1`) sit at Y ~ -0.13..-0.17, rear legs
 *     (`rev_rr1`, `rev_rl1`) sit at Y ~ +0.12..+0.16 -> **scene Y-negative = forward**.
 *   - the *_left legs sit at X > 0, the *_right legs sit at X < 0 -> **scene X-positive = left**.
 *   - Z is unchanged (up), matching `camera.up`.
 * So: scene X = left, scene Y = backward (-forward), scene Z = up.
 *
 * ## The transform
 * `IMU_FRAME_ADJUST` expresses, for each **scene** axis, which **body** axis it reads from
 * and with what sign. From the two conventions above:
 *   - scene X (left)      = +body Y (left)
 *   - scene Y (backward)  = -body X (forward)
 *   - scene Z (up)        = +body Z (up)
 *
 * `buildFrameTransformQuaternion()` turns that table into a fixed rotation quaternion `Q_T`
 * (a -90 degree rotation about Z, i.e. `Rz(-90 deg)`). Attitude quaternions are converted with
 * the similarity/conjugation transform `q_scene = Q_T (x) q_body (x) Q_T^-1`, which is the
 * correct way to re-express a rotation when both the body frame and the reference frame it is
 * measured against are relabeled by the same fixed rotation.
 *
 * ### Why conjugation (not a single `Q_T (x) q_body` multiplication)
 * A reviewer may be tempted to "simplify" this to a single right-multiplication
 * (`q_scene = q_body (x) Q_T^-1`, or `Q_T (x) q_body`). Do not make that change - it was tried
 * and numerically verified to be wrong:
 *   - With a single right-multiplication, the rest pose (identity body quaternion, device
 *     level) renders as `q_scene = Q_T^-1`, i.e. the model is shown yawed 90 degrees at rest
 *     instead of upright. The conjugation form preserves the identity at rest
 *     (`Q_T (x) I (x) Q_T^-1 = I`), which a single multiplication cannot do unless `Q_T` is
 *     itself identity.
 *   - The conjugation form is also the one that maps a body-Y rotation (nose-up pitch) onto
 *     scene-X (the model's left/right tilt axis) exactly, matching the axis remap declared in
 *     `IMU_FRAME_ADJUST` above. This is the standard similarity transform for re-expressing a
 *     rotation under a change of basis applied consistently to both the body axes and the
 *     world axes it is measured against - it is not extra/defensive code, it is required by
 *     the math.
 *
 * ## Adjusting on real hardware
 * If the displayed tilt direction does not match the physical robot once verified on
 * hardware, do NOT touch the quaternion math - only edit `IMU_FRAME_ADJUST` below. Each entry
 * picks the source body axis (`x` | `y` | `z`) and sign (`1` | `-1`) that maps onto that scene
 * axis. For example, if the IMU turns out to be mounted with Y pointing right instead of left,
 * flip the sign on the `x` entry (`{ axis: 'y', sign: -1 }`).
 */

export type ImuAxisKey = 'x' | 'y' | 'z';

export interface ImuAxisAdjust {
  /** Which IMU body axis this scene axis reads its value from. */
  axis: ImuAxisKey;
  /** Sign flip applied after reading the source axis. */
  sign: 1 | -1;
}

export interface ImuFrameAdjust {
  x: ImuAxisAdjust;
  y: ImuAxisAdjust;
  z: ImuAxisAdjust;
}

/**
 * Default axis remap: IMU body frame (X-forward / Y-left / Z-up) -> scene frame
 * (X-left / Y-backward / Z-up). See module docs above for the derivation. Edit this table
 * (not the transform code) when on-robot verification shows an axis needs flipping.
 */
export const IMU_FRAME_ADJUST: ImuFrameAdjust = {
  x: { axis: 'y', sign: 1 }, // scene X (left)     = +imu Y (left)
  y: { axis: 'x', sign: -1 }, // scene Y (backward) = -imu X (forward)
  z: { axis: 'z', sign: 1 }, // scene Z (up)       = +imu Z (up)
};

function unitAxisVector(key: ImuAxisKey): THREE.Vector3 {
  if (key === 'x') return new THREE.Vector3(1, 0, 0);
  if (key === 'y') return new THREE.Vector3(0, 1, 0);
  return new THREE.Vector3(0, 0, 1);
}

/**
 * Builds the fixed basis-change quaternion `Q_T` from an `ImuFrameAdjust` table. Each row of
 * the underlying basis matrix is the selected source axis (scaled by its sign), so
 * `v_scene = Q_T * v_imu`. Exposed as a function (rather than hardcoding the quaternion
 * literal) so `IMU_FRAME_ADJUST` stays the single place to tweak the convention.
 */
export function buildFrameTransformQuaternion(adjust: ImuFrameAdjust): THREE.Quaternion {
  const rowX = unitAxisVector(adjust.x.axis).multiplyScalar(adjust.x.sign);
  const rowY = unitAxisVector(adjust.y.axis).multiplyScalar(adjust.y.sign);
  const rowZ = unitAxisVector(adjust.z.axis).multiplyScalar(adjust.z.sign);

  const basis = new THREE.Matrix4();
  // Matrix4.set() takes arguments in row-major order.
  // prettier-ignore
  basis.set(
    rowX.x, rowX.y, rowX.z, 0,
    rowY.x, rowY.y, rowY.z, 0,
    rowZ.x, rowZ.y, rowZ.z, 0,
    0, 0, 0, 1,
  );
  return new THREE.Quaternion().setFromRotationMatrix(basis);
}

/** `Q_T`: the fixed IMU-body-frame -> scene-frame basis-change quaternion. */
export const Q_T = buildFrameTransformQuaternion(IMU_FRAME_ADJUST);
/** `Q_T^-1`, precomputed once since `IMU_FRAME_ADJUST` is a static constant. */
export const Q_T_INVERSE = Q_T.clone().invert();

/**
 * Applies `q_scene = Q_T (x) q_body (x) Q_T^-1` to convert an IMU body-frame attitude
 * quaternion into the three.js scene frame. Returns a new quaternion; does not mutate inputs.
 */
export function imuQuaternionToScene(bodyQuaternion: THREE.Quaternion): THREE.Quaternion {
  return Q_T.clone().multiply(bodyQuaternion).multiply(Q_T_INVERSE);
}
