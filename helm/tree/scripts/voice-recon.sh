#!/usr/bin/env bash
# HELM · почему голосовое не расшифровалось. Только чтение.
set -uo pipefail
cd /opt/helm/compose || exit 1
echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ ОКРУЖЕНИЕ ВОРКЕРА ############"
sudo docker compose exec -T helm-knowledge-worker sh -c '
  echo "ffmpeg: $(command -v ffmpeg || echo НЕТ)"
  echo "веса gigaam:"; ls -la /opt/helm/knowledge-worker/gigaam-models 2>&1 | head -10
  python3 - <<PY
for name in ("gigaam", "torch", "torchaudio", "silero_vad"):
    try:
        __import__(name)
        print(f"импорт {name}: ок")
    except Exception as exc:
        print(f"импорт {name}: ПРОВАЛ {type(exc).__name__}: {exc}")
PY
'

echo "############ ЛОГ ВОРКЕРА ПО ГОЛОСУ ############"
sudo docker compose logs --since 6h --no-color helm-knowledge-worker 2>&1 \
  | grep -iE "voice|голос|Traceback|Error|упал" | tail -40

echo "############ ГОТОВО ############"
