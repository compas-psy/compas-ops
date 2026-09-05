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

# Редактор не должен быть доступен снаружи. Одного кода 200 тут мало:
# панель — SPA, и Caddy отдаёт её index.html на ЛЮБОЙ неизвестный путь.
# Прогон 290 так и сделал: четыре «200», из которых ни одно ничего не
# доказывало. Смотрим не код, а КТО ответил.
probe_editor() {
  local path="$1" body marker
  body=$(curl -s -m 8 --noproxy '*' "https://helm.cmpas.ru$path" 2>/dev/null | head -c 4000)
  if printf '%s' "$body" | grep -qi 'n8n-app\|window.REST_ENDPOINT\|n8n.io\|__n8n'; then
    marker="РЕДАКТОР N8N ОТВЕЧАЕТ"
  elif printf '%s' "$body" | grep -qi 'HELM'; then
    marker="панель HELM (SPA-заглушка, не n8n)"
  elif [ -z "$body" ]; then
    marker="пусто"
  else
    marker="что-то другое"
  fi
  verdict "снаружи $path" "$(http "https://helm.cmpas.ru$path") · $marker"
}
for path in "/n8n/" "/rest/login" "/workflow" "/rest/settings"; do
  probe_editor "$path"
done

echo "-- ключ шифрования --"
# Спрашиваем сам контейнер, а не догадываемся по файлам: ключ может быть
# задан в compose, в env-файле или в окружении. Печатается СЧЁТЧИК,
# значение не покидает сервер ни при каком исходе.
inenv=$(sudo docker compose exec -T n8n printenv 2>/dev/null \
        | grep -c '^N8N_ENCRYPTION_KEY=..*')
inenv=${inenv:-0}
verdict "ключ в окружении контейнера (переменных с непустым значением)" "$inenv"
found_files=$(sudo grep -rls 'N8N_ENCRYPTION_KEY' /opt/helm/compose /etc/helm/secrets 2>/dev/null | wc -l)
verdict "файлов конфигурации, где встречается имя ключа" "$found_files"
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
# Сколько вообще workflow заведено — видно из БД n8n, без её API и без
# единого значения учётных данных: только счётчик строк.
wf=$(sudo docker exec helm-postgres-1 psql -U helm -d n8n -tAc \
     "select count(*) from workflow_entity" 2>/dev/null | tr -d ' ')
cred=$(sudo docker exec helm-postgres-1 psql -U helm -d n8n -tAc \
       "select count(*) from credentials_entity" 2>/dev/null | tr -d ' ')
verdict "workflow заведено (из БД n8n)" "${wf:-не прочиталось}"
verdict "учётных данных заведено (счётчик, не значения)" "${cred:-не прочиталось}"
verdict "хотя бы один реальный коннектор работает" "ТРЕБУЕТ ВЛАДЕЛЬЦА (запуск коннектора)"
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
