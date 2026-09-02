#!/bin/bash
# HELM v4.0 RESCUE · R1: что НА САМОМ ДЕЛЕ лежит в последнем снапшоте.
# Read-only, только метаданные — на нестабильном WebDAV это дёшево.
#
# Повод. `restic stats --mode raw-data` по всем 32 снапшотам даёт
# 60 МиБ несжатых, 9.6 МиБ хранимых, коэффициент 6.23x. Так выглядит
# текст, а не 62 МБ медицинских PDF: они уже сжаты и в 6 раз не
# ужимаются. Значит либо Vault в бэкапе нет вовсе, либо я неверно читаю
# статистику. Оба варианта надо закрыть фактом, а не рассуждением:
# «в бэкапе есть корпус» — утверждение, на котором держится вся
# необратимая часть R1.
set -uo pipefail

R() {
  sudo timeout 300 env \
    RESTIC_REPOSITORY="rclone:yandex:helm-backup" \
    RESTIC_PASSWORD_FILE="/etc/helm/secrets/restic_password" \
    RCLONE_TIMEOUT=2m RCLONE_CONTIMEOUT=1m \
    restic "$@"
}

echo "############ 1. КАКИЕ ПУТИ ЗАЯВЛЕНЫ В ПОСЛЕДНЕМ СНАПШОТЕ ############"
R snapshots latest --json 2>&1 | python3 -c "
import json, sys
raw = sys.stdin.read()
try:
    for s in json.loads(raw):
        print('снапшот', s['short_id'], s['time'][:19])
        for p in s.get('paths', []):
            print('   ', p)
except Exception:
    print(raw[:2000])
"

echo
echo "############ 2. ЕСТЬ ЛИ ТАМ ФАЙЛЫ VAULT ############"
# Пути внутри снапшота — абсолютные, поэтому спрашиваем прямо про каталог.
echo "--- /opt/helm-knowledge ---"
R ls latest /opt/helm-knowledge 2>&1 | head -20
echo "--- рекурсивно: сколько файлов под sources/ и raw/ ---"
# ВНИМАНИЕ: у `restic ls` короткий -r это --repo, а не «рекурсивно».
# Первый заход этой разведки написал `ls -r latest ...` и получил ноль —
# ноль от неверного флага, а не от пустого каталога. Полное имя флага.
R ls latest --recursive /opt/helm-knowledge 2>&1 \
  | awk '/^\/opt\/helm-knowledge\/sources\/./ {s++}
         /^\/opt\/helm-knowledge\/raw\/./      {r++}
         END {printf "  sources/: %d, raw/: %d\n", s, r}'

echo "--- размер самых крупных файлов снапшота ---"
R ls latest --long --recursive 2>&1 \
  | awk '$1 ~ /^-/ {print $4, $NF}' | sort -rn | head -8

echo
echo "############ 3. СКОЛЬКО ЭТО ПО ОБЪЁМУ ############"
R stats latest --mode restore-size 2>&1 | tail -6

echo
echo "############ 4. ЧТО НА ДИСКЕ ДЛЯ СРАВНЕНИЯ ############"
echo "общий Vault:      $(sudo find /opt/helm-knowledge -type f | wc -l) файлов, $(sudo du -sh /opt/helm-knowledge | cut -f1)"
echo "приватное дерево: $(sudo find /opt/helm-knowledge-private -type f | wc -l) файлов, $(sudo du -sh /opt/helm-knowledge-private | cut -f1)"

echo
echo "############ ГОТОВО ############"
