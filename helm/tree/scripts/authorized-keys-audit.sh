#!/usr/bin/env bash
# HELM · открывает ли сервер ключ, утёкший в публичный репозиторий.
# Только чтение.
#
# ЗАЧЕМ. `state/11_HUMAN_QUEUE.md`, H-260818-02: в публичном репозитории
# `compas-psy/cmpas.ru` лежал приватный SSH-ключ с комментарием
# `eliah@Eliah`, отпечаток `SHA256:VK0R69yEzaIjnQ5FfoKtI8Ji4f7b2x4q0QFMn0OTmDk`.
# Пункт «отозвать» открыт с 18.08.2026, последняя запись в очереди —
# 24.08. Сегодня 07.09. Двадцать дней.
#
# Один факт определяет срочность: если этот отпечаток есть в
# `authorized_keys` на сервере, любой, кто склонировал репозиторий за
# это время, может зайти. Если нет — утечка остаётся утечкой, но сервер
# ею не открывается.
#
# ЧТО ЗДЕСЬ НЕ ПЕЧАТАЕТСЯ. Ничего приватного. `authorized_keys` содержит
# ПУБЛИЧНЫЕ ключи, и печатаются только их отпечатки и комментарии —
# ровно то, чем ключи и опознают (CLAUDE.md §5.4: агент не логирует
# секреты; отпечаток публичного ключа секретом не является).
set -uo pipefail

LEAKED="SHA256:VK0R69yEzaIjnQ5FfoKtI8Ji4f7b2x4q0QFMn0OTmDk"

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"
echo "ищем отпечаток: $LEAKED"

echo
echo "############ КЛЮЧИ, ОТКРЫВАЮЩИЕ СЕРВЕР ############"
found=0
for home in /root /home/*; do
  file="$home/.ssh/authorized_keys"
  [ -r "$file" ] || file=""
  if [ -z "$file" ]; then
    sudo test -r "$home/.ssh/authorized_keys" && file="$home/.ssh/authorized_keys"
  fi
  [ -n "$file" ] || continue
  echo "— $file:"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    case "$line" in \#*) continue ;; esac
    printed=$(printf '%s\n' "$line" | sudo ssh-keygen -lf /dev/stdin 2>/dev/null)
    [ -n "$printed" ] || { echo "    (строка не разобрана как ключ)"; continue; }
    echo "    $printed"
    case "$printed" in *"${LEAKED#SHA256:}"*) echo "    ⚠️  ЭТО УТЁКШИЙ КЛЮЧ"; found=1 ;; esac
  done < <(sudo cat "$file" 2>/dev/null)
done

echo
echo "############ ВЫВОД ############"
if [ "$found" = 1 ]; then
  echo "⚠️  Утёкший ключ ОТКРЫВАЕТ этот сервер. Отзыв — не отложимая задача."
else
  echo "Утёкшего отпечатка среди authorized_keys нет: сервер этим ключом не открывается."
  echo "Это НЕ значит, что ключ отозван — он мог давать доступ к GitHub или другим"
  echo "системам, куда агент не смотрит. Пункт 1 H-260818-02 остаётся за владельцем."
fi

echo
echo "############ ВХОДЫ ПО SSH ЗА НЕДЕЛЮ ############"
sudo journalctl -u ssh -u sshd --since "7 days ago" 2>/dev/null \
  | grep -i "Accepted" | awk '{print $1, $2, $3, $9, $11}' | sort | uniq -c | tail -15 \
  || echo "  журнал sshd недоступен"
