# Батч 01 — что нужно сделать на сервере (29.08.2026)

Шаги 1–3 ниже уже выполнены 29.08.2026 (оставлены как есть — точный
протокол того, что было сделано и почему; полезно при повторном деплое
с нуля). Шаг 3 нашёл, что план «написать плагин Hermes» был лишним —
у Hermes уже есть встроенный API для этого; продолжение — отдельный
файл `hermes-enable-runbook.md`. Шаги 4–5 ещё не выполнены.

Ключ подключения указывается явно везде:
`-i "C:\Users\eliah\.ssh\helm_deploy_key"`.
Секреты не попадают в переписку: их кладёт на сервер твоя же команда
через `sudo tee`, я их не вижу и не запрашиваю.

Подробности по блокам — `forgejo-migrate-runbook.md`,
`n8n-export-runbook.md`, `max-bringup-runbook.md`.

---

## Шаг 1. Доставить на сервер код и скрипты — ✅ выполнено

Новый код Control Plane (вебхук MAX, очередь исходящих) на сервере ещё
не стоит — там работает образ до этих правок. Сначала доставка, сборка
и запуск будут на шаге 2, после того как появятся секреты (иначе
compose не поднимет контейнер: он ссылается на файлы, которых нет).

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\compose\docker-compose.yml helm\tree\scripts\forgejo-migrate.py helm\tree\scripts\n8n-workflows.py helm\tree\scripts\max-register-webhook.sh helm\tree\scripts\install-ru-ca.sh helm\tree\scripts\backup.sh helm\tree\scripts\restore_test.sh helm@185.250.44.137:/tmp/
```

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "test -d /opt/helm/control-plane/helm_core || { echo 'СТОП: ожидаемого пути нет, ничего не трогаю'; exit 1; }; sudo rm -rf /opt/helm/control-plane/helm_core && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo mv /tmp/docker-compose.yml /opt/helm/compose/docker-compose.yml && sudo mv /tmp/forgejo-migrate.py /tmp/n8n-workflows.py /tmp/max-register-webhook.sh /tmp/install-ru-ca.sh /tmp/backup.sh /tmp/restore_test.sh /opt/helm/scripts/ && sudo chown -R root:root /opt/helm/control-plane/helm_core /opt/helm/compose/docker-compose.yml /opt/helm/scripts && sudo chmod -R go-w /opt/helm/control-plane/helm_core /opt/helm/scripts && sudo chmod 755 /opt/helm/scripts/*.py /opt/helm/scripts/*.sh && sudo mkdir -p /opt/helm/n8n/exports && ls -la /opt/helm/scripts/ && ls /opt/helm/control-plane/helm_core/"
```

`chmod -R go-w` не косметика: scp создаёт каталоги world-writable даже
при правильном umask (F-260827-01).

Ожидается: в `helm_core/` видны `channels`, `dispatch.py`,
`hermes_bridge.py`; в `scripts/` шесть файлов.

---

## Шаг 2. Секреты MAX → пересборка → регистрация вебхука — ✅ выполнено

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

Секрет обязательно шестнадцатеричный (`openssl rand -hex`), а не
base64: MAX принимает ограниченный алфавит и отвергает `/` и `+` с
ошибкой `proto.payload`, ВОЗВРАЩАЯ присланный секрет открытым в тексте
ошибки (найдено вживую 29.08.2026).

**2.1. Положить секреты и поднять новый образ:**

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'ТОКЕН_БОТА' | sudo tee /etc/helm/secrets/max_bot_token > /dev/null && openssl rand -hex 32 | sudo tee /etc/helm/secrets/max_webhook_secret > /dev/null && echo '0' | sudo tee /etc/helm/secrets/max_owner_id > /dev/null && sudo chown root:helm-secrets /etc/helm/secrets/max_bot_token /etc/helm/secrets/max_webhook_secret /etc/helm/secrets/max_owner_id && sudo chmod 640 /etc/helm/secrets/max_bot_token /etc/helm/secrets/max_webhook_secret /etc/helm/secrets/max_owner_id && cd /opt/helm/compose && sudo docker compose build helm-core && sudo docker compose up -d helm-core && sleep 15 && sudo docker compose ps helm-core"
```

В команде подставляется одно значение — `ТОКЕН_БОТА`.

Права `640 root:helm-secrets`, а не `600 root:root`: эти файлы читает
контейнер от непривилегированного пользователя через группу, и `600`
сломали бы его старт (F-260829-09).

Ожидается: `helm-core` в состоянии `healthy`. Если контейнер
перезапускается — сразу `sudo docker compose logs --tail 50 helm-core`
и вывод в чат.

**2.1a. Доверие корню Минцифры (иначе MAX недоступен вовсе):**

Сертификат `platform-api2.max.ru` выдан «Russian Trusted Sub CA», и в
стандартном наборе корней Ubuntu его нет — curl отвергает соединение
(«unable to get local issuer certificate»). Основание для установки:
решение учредителя от 18.08.2026, принятое для платёжного шлюза
Т-Банк в `cmpas.ru`. Скрипт сверяет отпечатки скачанных сертификатов с
эталонными из того прогона и при расхождении не ставит ничего.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo /opt/helm/scripts/install-ru-ca.sh && cd /opt/helm/compose && sudo docker compose up -d --force-recreate helm-core && sleep 15 && sudo docker compose ps helm-core"
```

Ожидается «отпечаток совпал с эталонным» дважды и «MAX ответил кодом
401» — 401 здесь успех: TLS проверен, а без токена сервер и должен
отказать. Пересоздание контейнера обязательно: связка монтируется в
него отдельным томом.

**2.2. Зарегистрировать вебхук в MAX:**

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo /opt/helm/scripts/max-register-webhook.sh"
```

Скрипт, а не строка с JSON: тело запроса с вложенными кавычками,
проходящее через PowerShell → ssh → bash, ломается на экранировании —
это уже дважды ловилось на этом сервере (F-260828-01, F-260828-02).
В скрипте кавычки видит только bash, и вопрос исчезает. Секреты он
читает на месте, в переписку они не попадают.

Ожидается `HTTP 200`, затем список подписок с адресом
`https://helm.cmpas.ru/hooks/max`. Проверка подписки отдельным
запросом — не дублирование: ответ на регистрацию и реально записанная
на стороне MAX подписка это разные утверждения.

**2.3. Написать боту в MAX любое сообщение**, затем узнать свой id:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose logs --tail 30 helm-core | grep 'hooks/max'"
```

Ожидается строка `сообщение от не-владельца, sender_id=…`. Это число —
твой id в MAX (он не совпадает с Telegram-id). Записать его:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'ЧИСЛО_ИЗ_ЛОГА' | sudo tee /etc/helm/secrets/max_owner_id > /dev/null && cd /opt/helm/compose && sudo docker compose up -d --force-recreate helm-core && sleep 15 && sudo docker compose ps helm-core"
```

**2.4. Написать боту ещё раз.** Ожидаемый ответ в MAX (и полученный
вживую 29.08.2026, четыре раза подряд): «HELM принял сообщение, но
агент сейчас недоступен» — правильный результат на тот момент: API
Hermes ещё не был включён (см. шаг 3 и `hermes-enable-runbook.md`),
поэтому до модели сообщение не доходило; зато этот ответ доказал разом
всё остальное: секрет вебхука, сверку владельца, регистрацию задачи,
очередь исходящих и формат вызова MAX API (`chat_id` query-параметром —
единственное, что нельзя было проверить офлайн).

Если ответ не пришёл — вывод в чат:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose logs --tail 40 helm-core"
```

---

## Шаг 3. Разведка Hermes — ✅ выполнено, план изменился

Шесть заходов разведки (`scripts/hermes-recon.sh` — `hermes-recon-6.sh`,
все read-only) нашли, что план «написать Hermes-side плагин max-bridge»
был не нужен: у Hermes уже есть встроенный OpenAI-совместимый API-сервер
(`POST /v1/responses`), просто выключенный. `conversation` в теле
запроса — это и есть «named conversation» из §10.2, только выключенный
за отсутствием `API_SERVER_KEY`. Подробности и почему остальные три
рассмотренных пути (регистрация платформы, `_dispatch_plugin_message_
injection`, bundled-плагин) не годились — `docs/adr/ADR-020-max-responses-api.md`.

**Продолжение MAX — отдельный файл: `scripts/hermes-enable-runbook.md`.**
Он включает этот API (два места с одним ключом: Docker secret Control
Plane + `~/.hermes/.env` у Hermes), доставляет новый код Control Plane
(клиент API вместо HMAC-листенера) и доводит канал до настоящего ответа
chief-агента в MAX.

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
