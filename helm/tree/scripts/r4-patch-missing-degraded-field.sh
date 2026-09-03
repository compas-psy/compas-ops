#!/bin/bash
# HELM v4.0 RESCUE · R4 — одноразовый патч для run 200 (канонический
# golden benchmark на трёх кандидатах, gemma2:2b/qwen2.5:3b/qwen2.5:7b).
#
# run_candidate() пытался дописать other_services_degraded в
# resources-<model>.json через `python3 -c "... = $degraded"`, где
# $degraded — bash-строка "true"/"false". Это не валидные Python
# литералы (нужны True/False) — строка падала с NameError на ВСЕХ трёх
# кандидатах молча (stdout/stderr шёл в общий лог job'а, script
# продолжал), и поле никогда не попадало в файл. Баг в самом скрипте
# уже исправлен отдельно (аргументы через argv, не интерполяция).
#
# Значение false для всех трёх кандидатов — не предположение, а прямое
# показание job log run 200: "helm-core: до=OK после=OK" и
# "postgres: до=OK после=OK" для каждого кандидата (health не
# деградировало ни разу), что и есть единственное условие, при котором
# run_candidate() выставляет degraded="false". Патчим ТОЛЬКО если поле
# действительно отсутствует — идемпотентно, не перезаписывает
# существующее значение вслепую.
set -uo pipefail
BASE_DIR=/opt/helm-state/benchmarks/r4

for safe in gemma2_2b qwen2_5_3b qwen2_5_7b; do
  f="$BASE_DIR/resources-$safe.json"
  if [ ! -s "$f" ]; then
    echo "::error::$f не найден"
    exit 1
  fi
  sudo python3 -c "
import json
p = '$f'
d = json.load(open(p))
if 'other_services_degraded' in d:
    print('$safe: поле уже есть (' + repr(d['other_services_degraded']) + ') — не трогаю')
else:
    d['other_services_degraded'] = False
    json.dump(d, open(p, 'w'), indent=2)
    print('$safe: добавлено other_services_degraded=False')
"
done

echo
echo "############ ПРОВЕРКА ############"
for safe in gemma2_2b qwen2_5_3b qwen2_5_7b; do
  sudo cat "$BASE_DIR/resources-$safe.json"
  echo
done
