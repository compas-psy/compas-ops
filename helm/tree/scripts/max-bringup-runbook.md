# Ранбук: ввод канала MAX (ТЗ §10, ADR-020)

Подготовлено 29.08.2026. Сторона Control Plane уже написана и покрыта
тестами; здесь — то, что можно сделать только на сервере и только руками
владельца. Два независимых блока, порядок между ними произвольный:
**разведка** (нужна, чтобы дописать плагин Hermes) и **секреты**
(нужны, чтобы вебхук заработал).

Все команды идут с явным ключом, как заведено:
`-i "C:\Users\eliah\.ssh\helm_deploy_key"`.

## Блок 1. Разведка Hermes (read-only, ничего не меняет)

Плагин `max-bridge` вбрасывает синтетическое событие в тот же
gateway-dispatch, которым идут Telegram-сообщения. Четыре факта, которых
не хватает, чтобы написать его без догадок. Вывод вставить в чат целиком.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /home/helm/.hermes/hermes-agent && echo '=== 1. класс события ===' && grep -rn 'class .*Event' --include=*.py gateway/ | head -20 && echo '=== 2. dispatch ===' && grep -rn 'def .*dispatch\|pre_gateway_dispatch' --include=*.py gateway/ | head -20 && echo '=== 3. adapters ===' && grep -rn 'adapters\[\|class .*Adapter\|async def send' --include=*.py gateway/ | head -30 && echo '=== 4. platform enum ===' && grep -rn 'class Platform\|TELEGRAM =' --include=*.py . | head -10"
```

Если вывод какого-то блока пустой — значит структура каталогов другая;
тогда достаточно `find /home/helm/.hermes/hermes-agent -name '*.py' |
head -50`, и я сориентируюсь по именам файлов.

## Блок 2. Секреты MAX

Нужны три значения. Первые два берутся у бота MAX (создаётся в
`@MasterBot` в самом MAX, как `@BotFather` в Telegram), третье — твой
собственный числовой id в MAX.

| Секрет | Что это | Где взять |
|---|---|---|
| `max_bot_token` | токен бота | выдаётся при создании бота |
| `max_webhook_secret` | общий секрет вебхука | придумывается: `openssl rand -base64 24` |
| `max_owner_id` | твой user_id в MAX | **не** Telegram-id, см. ниже |

`max_owner_id` — отдельное число, не то же, что Telegram-id: мессенджеры
не делят пространство идентификаторов. Способ узнать без гаданий: положить
первые два секрета, зарегистрировать вебхук (блок 3) и написать боту в MAX
любое сообщение. Control Plane ответит 403 и запишет в лог строку
`hooks/max: сообщение от не-владельца, sender_id=…` — это число и есть
`max_owner_id`:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && docker compose logs --tail 50 helm-core | grep 'hooks/max'"
```

Логируется только идентификатор, текст сообщения — никогда. Первое
сообщение при этом не теряется: после установки секрета его достаточно
отправить заново.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "echo 'ТОКЕН_БОТА' | sudo tee /etc/helm/secrets/max_bot_token > /dev/null && echo 'СЕКРЕТ_ВЕБХУКА' | sudo tee /etc/helm/secrets/max_webhook_secret > /dev/null && echo 'ЧИСЛОВОЙ_ID' | sudo tee /etc/helm/secrets/max_owner_id > /dev/null && sudo chown root:helm-secrets /etc/helm/secrets/max_bot_token /etc/helm/secrets/max_webhook_secret /etc/helm/secrets/max_owner_id && sudo chmod 640 /etc/helm/secrets/max_bot_token /etc/helm/secrets/max_webhook_secret /etc/helm/secrets/max_owner_id && ls -la /etc/helm/secrets/ | grep max"
```

Права именно `root:helm-secrets 640`, а не `600 root:root`: эти три
файла читает контейнер `helm-core` от непривилегированного пользователя
через членство в группе (F-260829-09; интуитивно «более строгие» 600
ломают старт контейнера).

## Блок 3. Регистрация вебхука в MAX (после блока 2)

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "curl -sS -X POST https://platform-api2.max.ru/subscriptions -H \"Authorization: `$(sudo cat /etc/helm/secrets/max_bot_token)\" -H 'Content-Type: application/json' -d \"{\\\"url\\\":\\\"https://helm.cmpas.ru/hooks/max\\\",\\\"update_types\\\":[\\\"message_created\\\"],\\\"secret\\\":\\\"`$(sudo cat /etc/helm/secrets/max_webhook_secret)\\\"}\""
```

Секреты подставляются на самой машине через `sudo cat` и в переписку не
попадают. Обратный апостроф перед `$` обязателен — иначе подстановку
сделает PowerShell вместо bash (F-260828-02).

## Блок 4. Живая приёмка (§30.5, P7)

После деплоя плагина. Каждый пункт — отдельная проверка, вывод в чат:

1. **секрет-тест**: запрос на `/hooks/max` с неверным заголовком →
   403, задача не заводится;
2. **реальный вопрос из MAX** → ответ chief приходит в MAX;
3. **дедуп**: тот же вопрос в Telegram и в MAX в пределах 2 минут →
   одна задача, ответ только в Telegram (молчаливое схлопывание);
4. **`/force`**: `/force <тот же текст>` в MAX сразу после Telegram →
   вторая задача и отдельный ответ;
5. **n8n-down**: остановить n8n, повторить п.2 → MAX работает
   (§10: «MAX не должен зависеть от n8n»);
6. **chief-down**: остановить gateway Hermes, написать в MAX → приходит
   транспортное уведомление, задача остаётся REGISTERED.
