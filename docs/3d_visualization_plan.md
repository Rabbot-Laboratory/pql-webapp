# 3D Visualization Plan

## 1. Goal

今回の 3D 表示は、まず 1 脚分の URDF を使って Web UI 上にリアルタイム表示を出すことを目的にする。

- 現時点では `PQL-LG00-A1_description` の単脚モデルを使う
- 将来的には同じ脚を 4 本に増やした全身 URDF へ置き換える
- ただし UI 側の基本設計は「1 脚にフォーカスできる」まま維持する

## 2. Current Asset Summary

`PQL-LG00-A1_description` は ROS 2 の description パッケージとして成立している。

- `urdf/pql-lg00-a1.xacro`
  単脚モデル本体
- `meshes/*.stl`
  base, upper leg, lower leg のメッシュ
- `launch/robot_description.launch.py`
  `robot_state_publisher` 起動用 launch

現状の関節構成は 2 自由度。

- `rev1`
  `base_link -> PQL01-LU00-A1_v14_1`
- `rev2`
  `PQL01-LU00-A1_v14_1 -> leg_down_assy_v7_1`

当面はこれを以下のように対応づける。

- `rev1` = hip
- `rev2` = knee

## 3. Recommended Approach

最初の実装は、ROS 2 を必須にせず Web UI に直接 3D プレビューを載せる方式を第一候補にする。

理由:

- 今の `FastAPI + WebSocket` 構成に自然に乗る
- 操作画面と 3D 表示を同じブラウザで扱える
- ダミー ESP モードでも確認できる
- 後から全身 URDF に差し替えても UI 構造を保ちやすい

基本の流れ:

```text
ESP32 / Stub telemetry
  -> highend_server
  -> actuator state
  -> joint preview state
  -> WebSocket
  -> browser 3D viewer
```

## 4. Scope Split

### Phase A: 単脚 3D プレビュー

ここでやること:

- 単脚の hip / knee を 3D 上で動かす
- 実際の `position` に追従させる
- Web UI から見られる

ここではまだやらないこと:

- 四脚全身 URDF
- 物理シミュレーション
- 厳密な逆運動学
- ROS 2 必須化

### Phase B: 四脚 URDF への移行

将来やること:

- 単脚モデルを 4 本複製した全身 URDF を用意
- Front Right / Front Left / Rear Right / Rear Left を定義
- UI から脚ごとのフォーカス切り替え
- 全体表示と脚単位表示を両立

## 5. Data Design

単脚 3D プレビュー用に、既存の actuator state から joint 表示用データを作る。

### 5.1 Joint Preview State

最小構成:

```json
{
  "leg_id": "front_right",
  "hip": {
    "position": 2048,
    "angle_rad": 0.0
  },
  "knee": {
    "position": 2048,
    "angle_rad": 0.0
  },
  "updated_at": "2026-03-29T12:34:56+09:00"
}
```

### 5.2 Mapping Rule

当面は `position 0..4095` を `min_rad..max_rad` に線形マップする。

```text
position
  -> normalized 0..1
  -> joint angle(min_rad..max_rad)
```

初期値は仮置きでよい。

- hip:
  `-0.8 .. 0.8 rad`
- knee:
  `-1.2 .. 0.2 rad`

これは見た目合わせの暫定値で、後で実機に合わせて調整する。

## 6. UI Plan

### 6.1 Placement

最初は `Legs` タブの中に `Focused Leg 3D Preview` パネルを置く。

構成:

- 左:
  脚選択、hip/knee 数値、接続状態
- 右:
  3D 表示

単脚 URDF の段階では、表示対象は 1 脚固定でもよい。
ただし UI のラベルは将来の四脚化を見越しておく。

- `Focused Leg`
- `Leg Source`
- `Hip`
- `Knee`

### 6.2 First Visual Goal

最初の完成条件は次の 3 つ。

- 3D モデルがブラウザに表示される
- telemetry 更新で hip/knee が動く
- ダミー ESP モードでも動作確認できる

## 7. Backend Plan

### 7.1 Add Preview Mapping Layer

`highend_server` に joint preview 変換層を追加する。

候補:

- `src/highend_server/application/joint_preview.py`

責務:

- actuator state から hip/knee を読む
- 位置値を角度へ変換する
- UI へ返す preview payload を組み立てる

### 7.2 API / WebSocket

追加候補:

- `GET /api/preview/leg`
  現在の単脚 preview 状態
- `WebSocket event: leg_preview`
  hip / knee の角度更新通知

最初は既存の snapshot に混ぜず、独立イベントにした方が整理しやすい。

## 8. Frontend Plan

### 8.1 Short Term

いまのプレーン HTML/JS ベースなら、まずは次のどちらかで始める。

- 軽量な WebGL ベースビューアを埋め込む
- まずは 2D 骨格プレビューで先に動きを確認する

短期的には後者も十分有効。3D 本体導入前に hip/knee の対応が確認できる。

### 8.2 PrimeVue Phase

PrimeVue 移行後は、`Legs` タブに 3D カードを正式実装する。

- `Splitter`
- `Card`
- `Tag`
- `SelectButton`
- `Knob` または数値カード

## 9. Migration Strategy To Full Robot URDF

四脚化するときは、単脚プレビューの API と UI はできるだけ変えない。

変えるのは主にモデル側。

- 単脚:
  `leg_id = front_right`
- 四脚:
  `leg_id = front_right | front_left | rear_right | rear_left`

UI 側は「今見ている脚の preview を表示する」設計にしておく。
これなら全身 URDF になっても、個別フォーカス表示をそのまま維持できる。

## 10. Implementation Order

1. 単脚 preview 用 md 整理
2. `joint_preview` 変換ロジック追加
3. 単脚 preview API / WebSocket 追加
4. Web UI に preview パネル追加
5. ダミー ESP モードで確認
6. 実機 position と見た目の角度レンジ調整
7. 四脚 URDF を別途作成して差し替え準備

## 11. Open Items

- hip と knee に対応する actuator ID をどう固定するか
- 左右脚で関節軸の向きが反転するか
- 将来の全身 URDF で base の座標系をどう置くか
- 3D 表示を最初から本物の URDF ビューアにするか、段階的に入れるか

## 12. Recommendation

まずは「単脚 3D プレビューを Web UI に出す」ことを最優先にする。

この段階では、

- 単脚モデル
- 2 関節
- 単純な線形マッピング
- ダミー ESP でも動く

で十分価値がある。

そのうえで、四脚 URDF は別タスクとして並行準備し、完成後に表示ソースだけ差し替えるのが最も安全。

## 13. Full Robot URDF Temporary Mapping

`pql-a00_description` を使う段階では、各脚の 3 関節を次のように扱う。

- `rev_*1`
  いったん固定で `0 rad`
- `rev_*2`
  hip に対応
- `rev_*3`
  knee に対応

脚ごとの対応表:

- Front Right
  - `rev_fr1` = fixed
  - `rev_fr2` = hip
  - `rev_fr3` = knee
- Front Left
  - `rev_fl1` = fixed
  - `rev_fl2` = hip
  - `rev_fl3` = knee
- Rear Right
  - `rev_rr1` = fixed
  - `rev_rr2` = hip
  - `rev_rr3` = knee
- Rear Left
  - `rev_rl1` = fixed
  - `rev_rl2` = hip
  - `rev_rl3` = knee

左右の見え方は UI 側でミラー表示して調整する。
そのため、左脚は右脚と同じ角度レンジでも見た目が反転して見えるようにする。
