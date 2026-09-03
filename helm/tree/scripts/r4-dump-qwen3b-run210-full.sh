#!/bin/bash
# HELM v4.0 RESCUE · R4.5.6.5 — read-only recon: полный result.json
# qwen2.5:3b из run 210. Первый общий дамп (r4-dump-run210-artifacts.sh)
# уместил только хвост этого файла в лог GitHub Actions (кэп на размер
# у инструмента чтения логов) — нужен по-кейсовый critical entity/event
# recall (§14.18), а не только агрегат, и без него не восстановить.
# Только cat одного уже существующего файла — не пере-прогон Ollama.
set -uo pipefail
sudo cat /opt/helm-state/benchmarks/r4/qwen2_5_3b-b1028b51172d67eb/result.json
