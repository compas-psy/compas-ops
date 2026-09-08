#!/usr/bin/env bash
# HELM · разведка перед пунктом 6: n8n, Forgejo, утёкший ключ. Только чтение.
#
# ЗАЧЕМ. Распоряжение владельца 07.09.2026, п.6 требует довести до
# исполнимого плана календарь/план дня в n8n и всё в Forgejo, что не
# требует нового секрета. Прежде чем писать сценарий и порядок миграции,
# надо знать ФАКТ, а не предположение: поднят ли n8n, есть ли ключ его
# API, есть ли в Forgejo репозитории и раннер.
#
# Ключи и пароли НЕ печатаются — только наличие файла (CLAUDE.md §5.4).
set -uo pipefail
cd /opt/helm/compose || exit 1

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo
echo "############ 1. N8N ############"
sudo docker compose ps n8n --format '  {{.Name}} {{.State}} {{.Status}}' 2>&1 | head -3
echo "— ключ API n8n (§17.5, нужен для выгрузки и импорта сценариев):"
if sudo test -f /etc/helm/secrets/n8n_api_key; then
  echo "  /etc/helm/secrets/n8n_api_key: ЕСТЬ ($(sudo stat -c '%A %U:%G' /etc/helm/secrets/n8n_api_key))"
else
  echo "  /etc/helm/secrets/n8n_api_key: НЕТ — импорт и выгрузка сценариев невозможны"
fi
echo "— отвечает ли n8n:"
sudo docker compose exec -T n8n sh -c 'wget -qO- --timeout=10 http://127.0.0.1:5678/healthz 2>&1 | head -c 200' 2>&1 | tail -2
echo
echo "— выгруженные сценарии:"
sudo ls -1 /opt/helm/n8n/exports/ 2>/dev/null | sed 's/^/  /' || echo "  каталога выгрузки нет"
echo "— сценарии в самой базе n8n:"
sudo docker compose exec -T postgres psql -U n8n -d n8n -tAc \
  "select name, active from workflow_entity order by name" 2>&1 | sed 's/^/  /' | head -10

echo
echo "############ 2. FORGEJO ############"
sudo docker compose ps forgejo --format '  {{.Name}} {{.State}} {{.Status}}' 2>&1 | head -3
echo "— версия и учётные записи:"
sudo docker compose exec -T forgejo sh -c 'forgejo --version 2>&1 | head -1' 2>&1 | tail -1
sudo docker compose exec -T forgejo sh -c 'forgejo admin user list 2>&1 | head -5' 2>&1 | tail -5
echo "— репозитории на диске:"
sudo find /var/lib/docker/volumes -maxdepth 6 -path '*forgejo*' -name '*.git' -type d 2>/dev/null | head -10 | sed 's/^/  /' || true
sudo docker compose exec -T forgejo sh -c 'ls -1 /data/git/repositories 2>/dev/null | head -10' 2>&1 | sed 's/^/  /' | tail -11
echo "— раннер Forgejo Actions:"
sudo docker compose ps 2>&1 | grep -i runner | sed 's/^/  /' || echo "  раннера в compose нет"
sudo systemctl list-units --all --no-pager 2>/dev/null | grep -i "forgejo\|act_runner" | sed 's/^/  /' || echo "  юнитов раннера нет"

echo
echo "############ 3. УТЁКШИЙ КЛЮЧ: ЧТО ВИДНО С СЕРВЕРА ############"
# Отпечаток из state/11_HUMAN_QUEUE.md (H-260818-02). Публичный отпечаток
# секретом не является; сам ключ здесь не печатается и не пересылается.
LEAKED="SHA256:VK0R69yEzaIjnQ5FfoKtI8Ji4f7b2x4q0QFMn0OTmDk"
echo "— ищем отпечаток $LEAKED среди authorized_keys:"
found=0
for home in /root /home/*; do
  ak="$home/.ssh/authorized_keys"
  sudo test -f "$ak" || continue
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    fp=$(printf '%s\n' "$line" | ssh-keygen -lf /dev/stdin 2>/dev/null | awk '{print $2}')
    [ -n "$fp" ] || continue
    if [ "$fp" = "$LEAKED" ]; then
      echo "  НАЙДЕН в $ak"; found=1
    fi
  done < <(sudo cat "$ak")
  echo "  $ak: ключей $(sudo grep -c '^ssh-' "$ak" 2>/dev/null || echo 0)"
done
[ "$found" = 0 ] && echo "  отпечатка нет ни в одном authorized_keys"
echo "— входы по SSH за неделю (кто и откуда):"
sudo journalctl -u ssh -u sshd --since "7 days ago" --no-pager 2>/dev/null \
  | grep "Accepted" | awk '{print $(NF-5), $(NF-3)}' | sort | uniq -c | sort -rn | head -10 | sed 's/^/  /'
