# Ранбук: включить встроенный API Hermes для MAX (ADR-020)

Заменяет прежний план «написать и задеплоить плагин `max-bridge`» —
разведка на сервере (`hermes-recon-*.sh`, 6 заходов) нашла, что у Hermes
уже есть встроенный OpenAI-совместимый API-сервер, просто выключенный.
Дальше — только конфигурация и рестарт, кода на стороне Hermes не будет.

Ключ подключения — везде явно:
`-i "C:\Users\eliah\.ssh\helm_deploy_key"`.

## 1. Сгенерировать ключ (один, кладётся в два места)

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "openssl rand -hex 32 | sudo tee /etc/helm/secrets/hermes_api_server_key > /dev/null && sudo chown root:helm-secrets /etc/helm/secrets/hermes_api_server_key && sudo chmod 640 /etc/helm/secrets/hermes_api_server_key && sudo cat /etc/helm/secrets/hermes_api_server_key | wc -c"
```

Права `640 root:helm-secrets`, не `600 root:root`: этот файл читает
контейнер `helm-core` от непривилегированного пользователя через
членство в группе (тот же паттерн, что у остальных секретов MAX,
F-260829-09).

## 2. То же значение — в `~/.hermes/.env`

Hermes работает на хосте, не в контейнере, и читает переменные из
`~/.hermes/.env` (тот же файл уже входит в `backup.sh`). Значение
должно СОВПАДАТЬ с шагом 1.

Отдельным скриптом, а не строкой в ssh: `$(sudo cat ...)` внутри
двойных кавычек PowerShell разворачивается ЛОКАЛЬНО, на Windows, ещё до
отправки на сервер (та же ловушка, что уже дважды ловилась —
F-260828-01/02, здесь поймана в третий раз, уже в этом ранбуке). Если
это уже пробовалось командой с `$(...)` и в ответ пришло что-то вроде
«Sudo is disabled on this machine» — это ошибка ЛОКАЛЬНОГО Windows
sudo, а не сервера, и в `~/.hermes/.env` на сервере скорее всего
осталась битая пустая строка `API_SERVER_KEY=`; скрипт ниже её найдёт и
заменит (идемпотентен), объяснять/чистить руками не нужно.

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\hermes-set-api-key.sh helm@185.250.44.137:/tmp/
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo bash /tmp/hermes-set-api-key.sh"
```

Ожидается «готово, строк API_SERVER_KEY= в файле: 1».

## 3. Перезапустить Hermes gateway

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo systemctl restart hermes-gateway && sleep 5 && sudo systemctl status hermes-gateway --no-pager | head -15"
```

Если имя сервиса другое — покажет `systemctl status`, дальше по нему.
Ожидается `active (running)`.

## 4. Проверить, что порт поднялся

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo ss -tlnp | grep 8642"
```

Ожидается `127.0.0.1:8642` — не `0.0.0.0:8642` и не отсутствие строки
вовсе (последнее means `API_SERVER_KEY` не подхвачен — проверить шаг 2
и лог `journalctl -u hermes-gateway -n 50`).

## 5. Живая диагностика самого API (до кода Control Plane)

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\hermes-responses-diagnose.sh helm@185.250.44.137:/tmp/
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo mv /tmp/hermes-responses-diagnose.sh /opt/helm/scripts/ && sudo chmod 755 /opt/helm/scripts/hermes-responses-diagnose.sh && sudo /opt/helm/scripts/hermes-responses-diagnose.sh"
```

Пришли вывод целиком — это последнее непроверенное место в ADR-020
(форма JSON-ответа). Если `output[].content[].type` там не
`"output_text"` — правка в одной функции
`helm_core/hermes_bridge.py::_extract_reply_text`, не в архитектуре.

## 6. Доставить новый код Control Plane и пересобрать

```powershell
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/control-plane/helm_core && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo chown -R root:root /opt/helm/control-plane/helm_core && sudo chmod -R go-w /opt/helm/control-plane/helm_core && cd /opt/helm/compose && sudo docker compose build helm-core && sudo docker compose up -d --force-recreate helm-core && sleep 15 && sudo docker compose ps helm-core"
```

Ожидается `healthy`. Убедиться, что действительно новый код (тот же
приём, что уже спасал от кэша сборки раньше):

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core grep -A2 'def conversation_name' /opt/helm/control-plane/helm_core/hermes_bridge.py"
```

## 7. Живой конец в конец через MAX

Написать боту в MAX любое сообщение (например «привет»). Ожидается
настоящий ответ от chief-агента (не транспортное уведомление о
недоступности — оно означало бы, что что-то из шагов 1-6 не сработало).

Если ответа нет — по порядку:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose logs --since 2m helm-core | grep -iv healthz"
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc 'select channel, recipient, status, attempts from outbox order by next_attempt_at desc limit 5;'"
```

Первая покажет, дошёл ли вызов `/v1/responses` и с каким кодом; вторая —
дошло ли что-то до очереди исходящих и в каком статусе.
