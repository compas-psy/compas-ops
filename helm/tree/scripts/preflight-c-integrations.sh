#!/bin/bash
# HELM · живая ре-верификация n8n и Forgejo перед вехой C.
#
# Распоряжение владельца 05.09.2026, §4: «Они не "уже однажды
# поставлены и забыты". Перед Milestone C acceptance сделать короткую
# live re-verification.»
#
# action=recon: только читает. Ни одного изменения.
#
# Чего этот скрипт НЕ делает и почему. Он не заводит и не пересылает
# токены n8n и Forgejo (устав §6.1, CLAUDE.md §5.4), поэтому пункты,
# требующие аутентифицированного API — список коннекторов, защита ветки
# main, список приватных репозиториев, — он проверить не может и честно
# помечает `ТРЕБУЕТ ВЛАДЕЛЬЦА`. Отчёт, в котором такие строки выданы за
# пройденные, хуже отсутствующего.
#
# Значения секретов не печатаются нигде: проверяется наличие имени, а не
# содержимое.
set -uo pipefail
cd /opt/helm/compose || exit 1

verdict() { printf '%-52s %s\n' "$1" "$2"; }
http() { curl -s -o /dev/null -m 8 -w '%{http_code}' --noproxy '*' "$1" 2>/dev/null || echo "нет ответа"; }

echo "выкачено: $(sudo cat /opt/helm/DEPLOYED_SHA 2>/dev/null || echo unknown)"

echo "############ N8N ############"
state=$(sudo docker compose ps --format '{{.Service}}\t{{.State}}' | awk -F'\t' '$1=="n8n"{print $2}')
verdict "Community поднят" "${state:-нет сервиса}"
verdict "локальный healthz" "$(http http://127.0.0.1:5678/healthz)"

# Редактор не должен быть доступен снаружи ни по одному пути, кроме
# точного OAuth-callback. Проверяем корень и типичные пути редактора.
for path in "/n8n/" "/n8n" "/rest/login" "/workflow"; do
  verdict "снаружи $path (ожидается не 200)" "$(http "https://helm.cmpas.ru$path")"
done

echo "-- ключ шифрования --"
if sudo grep -qs 'N8N_ENCRYPTION_KEY' /opt/helm/compose/.env /etc/helm/secrets/* 2>/dev/null; then
  verdict "N8N_ENCRYPTION_KEY заведён (имя, не значение)" "да"
else
  verdict "N8N_ENCRYPTION_KEY заведён (имя, не значение)" "НЕ НАЙДЕН"
fi
if sudo grep -qs 'N8N_ENCRYPTION_KEY' /opt/helm/scripts/backup.sh; then
  verdict "ключ входит в бэкап" "да"
else
  verdict "ключ входит в бэкап" "в backup.sh не упомянут"
fi

echo "-- выгрузка workflow --"
exports=/opt/helm/n8n/exports
verdict "файлов выгрузки" "$(sudo find $exports -name '*.json' -type f 2>/dev/null | wc -l)"
newest=$(sudo find $exports -name '*.json' -type f -printf '%T@ %p\n' 2>/dev/null | sort -rn | head -1 | cut -d' ' -f2-)
if [ -n "$newest" ]; then
  verdict "свежесть последней выгрузки" "$(sudo stat -c %y "$newest" | cut -d. -f1)"
  # Значения учётных данных в выгрузке недопустимы (§17.5). Ищем
  # признаки, а не сами значения: наличие непустого поля с секретом.
  leaks=$(sudo grep -l '"password"[[:space:]]*:[[:space:]]*"[^"]\+"\|"apiKey"[[:space:]]*:[[:space:]]*"[^"]\+"\|"accessToken"[[:space:]]*:[[:space:]]*"[^"]\+"' \
          "$exports"/*.json 2>/dev/null | wc -l)
  verdict "выгрузок с непустым секретом (ожидается 0)" "$leaks"
else
  verdict "свежесть последней выгрузки" "выгрузок нет"
fi
if [ -x /opt/helm/scripts/n8n-workflows.py ] || [ -f /opt/helm/scripts/n8n-workflows.py ]; then
  verdict "скрипт выгрузки/восстановления на месте" "да"
else
  verdict "скрипт выгрузки/восстановления на месте" "НЕТ"
fi
verdict "хотя бы один реальный коннектор" "ТРЕБУЕТ ВЛАДЕЛЬЦА (нужен API-ключ n8n)"
verdict "n8n не хранит канонического состояния" "свойство архитектуры, скриптом не мерится"

echo "############ FORGEJO / GITHUB ############"
state=$(sudo docker compose ps --format '{{.Service}}\t{{.State}}' | awk -F'\t' '$1=="forgejo"{print $2}')
verdict "Forgejo поднят" "${state:-нет сервиса}"
verdict "git.cmpas.ru снаружи" "$(http https://git.cmpas.ru/)"
verdict "git.cmpas.ru здоровье API" "$(http https://git.cmpas.ru/api/healthz)"

# Приватность по умолчанию: список репозиториев без ключа не должен
# отдавать содержимого. Публичный explore на приватной установке либо
# пуст, либо закрыт.
verdict "explore без входа (ожидается 200 с пустым списком или 403)" "$(http https://git.cmpas.ru/explore/repos)"

echo "-- бэкап Forgejo --"
if sudo grep -qs 'forgejo' /opt/helm/scripts/backup.sh; then
  verdict "репозитории и метаданные в бэкапе" "да (упомянут в backup.sh)"
else
  verdict "репозитории и метаданные в бэкапе" "НЕ УПОМЯНУТ"
fi

verdict "main защищён" "ТРЕБУЕТ ВЛАДЕЛЬЦА (нужен API-токен Forgejo)"
verdict "push mirror настроен и свеж" "ТРЕБУЕТ ВЛАДЕЛЬЦА (тот же токен)"
verdict "тот же SHA в GitHub и CI на нём" "проверяется прогонами Actions этой ветки"
verdict "Forgejo — основной remote агента" "НЕТ: сессия работает через GitHub (см. отчёт)"

echo "############ ГОТОВО ############"
exit 0
