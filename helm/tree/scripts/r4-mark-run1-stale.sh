#!/bin/bash
# HELM v4.0 RESCUE · R4 — ретракция владельца 02.09.2026 (п.2 второго
# сообщения): результаты recon #185/#186/#187 (каталог run1/, старый
# небезопасный скрипт на SHA a8304db, до fingerprint/lifecycle-safety/
# ResourceStats-hardening) объявлены diagnostic/stale — не могут
# участвовать в выборе winner и не засчитываются в R4 PASS. Не
# удаляются как forensic evidence, только помечаются явным маркером
# рядом. Идемпотентно: перезаписывает тот же файл при повторном запуске.
set -uo pipefail
RUN1_DIR=/opt/helm-state/benchmarks/r4/run1

if [ ! -d "$RUN1_DIR" ]; then
  echo "run1 отсутствует — нечего помечать (уже ок)"
  exit 0
fi

sudo tee "$RUN1_DIR/DIAGNOSTIC_STALE.json" > /dev/null <<'JSON'
{
  "eligible_for_selection": false,
  "eligible_for_R4_PASS": false,
  "reason": "run1/ - результаты recon #185/#186/#187 на старом небезопасном скрипте (SHA a8304db), до fingerprint/lifecycle-safety/ResourceStats-hardening из ретракций владельца 02.09.2026. #187 отменён вручную до завершения. Не переиспользовать: канонический benchmark пишет в отдельные fingerprint-каталоги, не в run1/."
}
JSON

# Реальная метка времени — на сервере, не в момент коммита в песочнице.
sudo python3 -c "
import json, datetime
p = '$RUN1_DIR/DIAGNOSTIC_STALE.json'
d = json.load(open(p))
d['marked_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
json.dump(d, open(p, 'w'), ensure_ascii=False, indent=2)
"

echo "############ Помечено ############"
sudo cat "$RUN1_DIR/DIAGNOSTIC_STALE.json"
