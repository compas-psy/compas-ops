# Батч 01 — что нужно сделать на сервере (29.08.2026)

Пять шагов по порядку. Шаг 1 обязан быть первым: он доставляет код и
скрипты, которыми работают остальные. Шаги 3–5 между собой независимы.

Ключ подключения указывается явно везде:
`-i "C:\Users\eliah\.ssh\helm_deploy_key"`.
Секреты не попадают в переписку: их кладёт на сервер твоя же команда
через `sudo tee`, я их не вижу и не запрашиваю.

Подробности по блокам — `forgejo-migrate-runbook.md`,
`n8n-export-runbook.md`, `max-bringup-runbook.md`.

---

## Шаг 1. Доставить на сервер код и скрипты

Новый код Control Plane (вебхук MAX, очередь исходящих) на сервере ещё
не стоит — там работает образ до этих правок. Сначала доставка, сборка
и запуск будут на шаге 2, после того как появятся секреты (иначе
compose не поднимет контейнер: он ссылается на файлы, которых нет).

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\compose\docker-compose.yml helm\tree\scripts\forgejo-migrate.py helm\tree\scripts\n8n-workflows.py helm\tree\scripts\backup.sh helm\tree\scripts\restore_test.sh helm@185.250.44.137:/tmp/
```

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "test -d /opt/helm/control-plane/helm_core || { echo 'СТОП: ожидаемого пути нет, ничего не трогаю'; exit 1; }; sudo rm -rf /opt/helm/control-plane/helm_core && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo mv /tmp/docker-compose.yml /opt/helm/compose/docker-compose.yml && sudo mv /tmp/forgejo-migrate.py /tmp/n8n-workflows.py /tmp/backup.sh /tmp/restore_test.sh /opt/helm/scripts/ && sudo chown -R root:root /opt/helm/control-plane/helm_core /opt/helm/compose/docker-compose.yml /opt/helm/scripts && sudo chmod -R go-w /opt/helm/control-plane/helm_core /opt/helm/scripts && sudo chmod 755 /opt/helm/scripts/*.py /opt/helm/scripts/*.sh && sudo mkdir -p /opt/helm/n8n/exports && ls -la /opt/helm/scripts/ && ls /opt/helm/control-plane/helm_core/"
```

`chmod -R go-w` не косметика: scp создаёт каталоги world-writable даже
при правильном umask (F-260827-01).

Ожидается: в `helm_core/` видны `channels`, `dispatch.py`,
`hermes_bridge.py`; в `scripts/` шесть файлов.

---

## Шаг 2. Секреты MAX → пересборка → регистрация вебхука

От тебя нужен **только токен бота** (в MAX: настройки бота →
Расширенные настройки → Чат-бот → Токен доступа).

Секрет вебхука в MAX не выдаётся и в его интерфейсе отсутствует
намеренно: это значение придумывает наша сторона и передаёт в MAX при
регистрации подписки (шаг 2.2), а MAX затем присылает его обратно в
заголовке `X-Max-Bot-Api-Secret` каждого вызова — так Control Plane
отличает настоящий вебхук от подделки. Команда ниже генерирует его на
самом сервере: он не проходит ни через буфер обмена, ни через
переписку. Третий секрет, `max_owner_id`, кладётся нулём — настоящее
число узнаётся на шаге 2.3.

**2.1. Положить секреты и поднять новый образ:**

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'ТОКЕН_БОТА' | sudo tee /etc/helm/secrets/max_bot_token > /dev/null && openssl rand -base64 24 | sudo tee /etc/helm/secrets/max_webhook_secret > /dev/null && echo '0' | sudo tee /etc/helm/secrets/max_owner_id > /dev/null && sudo chown root:helm-secrets /etc/helm/secrets/max_bot_token /etc/helm/secrets/max_webhook_secret /etc/helm/secrets/max_owner_id && sudo chmod 640 /etc/helm/secrets/max_bot_token /etc/helm/secrets/max_webhook_secret /etc/helm/secrets/max_owner_id && cd /opt/helm/compose && sudo docker compose build helm-core && sudo docker compose up -d helm-core && sleep 15 && sudo docker compose ps helm-core"
```

В команде подставляется одно значение — `ТОКЕН_БОТА`.

Права `640 root:helm-secrets`, а не `600 root:root`: эти файлы читает
контейнер от непривилегированного пользователя через группу, и `600`
сломали бы его старт (F-260829-09).

Ожидается: `helm-core` в состоянии `healthy`. Если контейнер
перезапускается — сразу `sudo docker compose logs --tail 50 helm-core`
и вывод в чат.

**2.2. Зарегистрировать вебхук в MAX:**

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "curl -sS -X POST https://platform-api2.max.ru/subscriptions -H \"Authorization: `$(sudo cat /etc/helm/secrets/max_bot_token)\" -H 'Content-Type: application/json' -d \"{\\\"url\\\":\\\"https://helm.cmpas.ru/hooks/max\\\",\\\"update_types\\\":[\\\"message_created\\\"],\\\"secret\\\":\\\"`$(sudo cat /etc/helm/secrets/max_webhook_secret)\\\"}\""
```

Обратный апостроф перед `$` обязателен — иначе подстановку сделает
PowerShell вместо bash, и на сервер уедет пустая строка (F-260828-02).
Секреты подставляются на самой машине и в переписку не попадают.

**2.3. Написать боту в MAX любое сообщение**, затем узнать свой id:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose logs --tail 30 helm-core | grep 'hooks/max'"
```

Ожидается строка `сообщение от не-владельца, sender_id=…`. Это число —
твой id в MAX (он не совпадает с Telegram-id). Записать его:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'ЧИСЛО_ИЗ_ЛОГА' | sudo tee /etc/helm/secrets/max_owner_id > /dev/null && cd /opt/helm/compose && sudo docker compose up -d --force-recreate helm-core && sleep 15 && sudo docker compose ps helm-core"
```

**2.4. Написать боту ещё раз.** Ожидаемый ответ в MAX: «HELM принял
сообщение, но агент сейчас недоступен» — это правильный результат на
данном этапе. Плагина Hermes ещё нет, поэтому до модели сообщение не
доходит; зато этот ответ доказывает разом всё остальное: секрет
вебхука, сверку владельца, регистрацию задачи, очередь исходящих и —
главное — что формат вызова MAX API угадан верно (единственное место,
которое я не мог проверить офлайн).

Если ответ не пришёл — вывод в чат:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose logs --tail 40 helm-core"
```

---

## Шаг 3. Разведка Hermes (токен не нужен)

Ничего не меняет, только читает. Нужна, чтобы дописать плагин
`max-bridge`: без этих четырёх фактов он пишется вслепую.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /home/helm/.hermes/hermes-agent && echo '=== 1. класс события ===' && grep -rn 'class .*Event' --include=*.py gateway/ | head -20 && echo '=== 2. dispatch ===' && grep -rn 'def .*dispatch\|pre_gateway_dispatch' --include=*.py gateway/ | head -20 && echo '=== 3. adapters ===' && grep -rn 'adapters\[\|class .*Adapter\|async def send' --include=*.py gateway/ | head -30 && echo '=== 4. platform enum ===' && grep -rn 'class Platform\|TELEGRAM =' --include=*.py . | head -10"
```

Вывод целиком — в чат. Если блок пуст, структура другая; тогда хватит
`find /home/helm/.hermes/hermes-agent -name '*.py' | head -50`.

---

## Шаг 4. Ключ API n8n → включается ночная выгрузка

Создать ключ в n8n через туннель:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" -L 5678:127.0.0.1:5678 helm@185.250.44.137
```

`http://127.0.0.1:5678` → Settings → n8n API → Create an API key, срок
максимальный. Туннель закрыть (Ctrl+C), ключ положить и сразу
проверить всю цепочку бэкапа:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'КЛЮЧ_API' | sudo tee /etc/helm/secrets/n8n_api_key > /dev/null && sudo chown root:root /etc/helm/secrets/n8n_api_key && sudo chmod 600 /etc/helm/secrets/n8n_api_key && sudo python3 /opt/helm/scripts/n8n-workflows.py export && sudo /opt/helm/scripts/backup.sh && sudo /opt/helm/scripts/restore_test.sh"
```

Здесь права `600 root:root` — этот секрет читает host-скрипт от root, а
не контейнер (обратный случай к шагу 2.1).

Ожидается: «всего workflow: 0» (их ещё нет — нормально), `BACKUP DONE`,
`RESTORE TEST PASSED`. Вывод в чат.

---

## Шаг 5. Токен GitHub → миграция репозиториев в Forgejo

Токен: github.com → Settings → Developer settings → Fine-grained tokens
→ Generate new token. Resource owner `compas-psy`; Only select
repositories: `compas-voice`, `cmpas.ru`, `zapiski`,
`signalAI-mobileApp`, `compas-ops`; Permissions → Repository →
**Contents: Read and write**, больше ничего; срок 90 дней.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'ВСТАВЬ_PAT' | sudo tee /etc/helm/secrets/github_mirror_pat > /dev/null && sudo chown root:root /etc/helm/secrets/github_mirror_pat && sudo chmod 600 /etc/helm/secrets/github_mirror_pat && sudo python3 /opt/helm/scripts/forgejo-migrate.py"
```

Мигрируются четыре репозитория; `compas-ops` — отдельной командой
позже, после merge текущей рабочей ветки (иначе зеркало затрёт свежие
GitHub-коммиты). Вывод целиком в чат — он идёт в
`implementation-state/MIGRATION-LOG.md`, как требует §18.3.
