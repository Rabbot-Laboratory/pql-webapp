# 実機診断プローブ集

**Pi上で実行する**(localhost:8000のAPIを叩く)。浮かせた状態・エア供給確認済みが前提。
アクチュエータが実際に動くので、実行前に必ず操作者の確認を取ること。
実行例: `ssh先で .venv/bin/python /tmp/axis_probe.py`(pi_deploy.pyで同期後は
`~/pql-webapp/scripts/probes/` にもある)。実験記録(/api/experiments/start)と併用推奨。

- `axis_probe.py` — 全軸±300ステップ応答(小誤差での追従性)
- `bang_probe.py` — 全軸0⇔4095フルストローク×5(最大駆動での速度・遅れ)
- `gain_tune_probe.py` — 基準ゲイン復元→P250/I60比較(ORIGINAL_GAINSを要更新)
- `gain_grid.py` — 軸別P×Iグリッド探索、ベストを適用(スコア=誤差+1.5×振動)
- `restore_gains.py` — ORIGINAL_GAINSへ一括復元+全軸2048へ

2026-08-23の診断結果: docs/experiments/2026-08-23_hardware_diagnosis.md
