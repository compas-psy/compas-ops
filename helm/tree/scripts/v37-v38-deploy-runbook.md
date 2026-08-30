# Ранбук: выкатить v3.7 + v3.8 одним заходом

Везёт на сервер всё, что накопилось и ни разу там не было:

- **v3.7** — загрузка ZIP-архивов пачкой (P8.5.2.1)
- **v3.8** — «Запомни» и поиск по ней (P8.5.12), разделение по людям и
  запрет на уровне базы (P8.6.1/8.6.3), отдельный Knowledge-бот
  (P8.6.2), лимиты и честная очередь (P8.6.4), раздел «Пользователи» и
  вход второго человека в панель (P8.6.5), приостановка и сброс passkey
  (часть P8.6.7)

Три миграции базы, пересборка двух контейнеров, новая сборка панели,
новый маршрут в Caddy, два новых секрета.

**Порядок обязателен.** Причины у каждого шага названы — это не
формальность: прошлые выкаты ловили по 2–4 живых бага, которых в
песочнице не видно.

---

## 0. Перед началом — снять точку возврата

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo /opt/helm/scripts/backup.sh"
```

Не пропускать. Дальше идут три миграции, одна из которых переписывает
все существующие строки базы знаний (проставляет им владельца). Откат
миграции написан и проверен, но точка возврата дешевле отката.

Запомнить, что было до: понадобится для сверки в шаге 9.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc 'select count(*) from knowledge_sources' && sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc 'select count(*) from knowledge_chunks'"
```

---

## 1. Проверить, что роль базы — не суперпользователь

**Самый важный шаг всего выката, и он занимает секунду.**

Разделение по людям держится на двух слоях: проверка в коде и запрет в
самой базе (RLS). Суперпользователь PostgreSQL обходит RLS **всегда**,
молча и без ошибки. Если роль приложения окажется суперпользователем,
второй слой превратится в декорацию, и узнать об этом будет неоткуда —
всё будет «работать».

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc \"select rolname, rolsuper from pg_roles where rolname in ('helm','helm_app')\""
```

Ожидается `f` (false) в колонке суперпользователя у роли, под которой
работает приложение. Если `t` — **остановиться и сообщить**, дальше
идти нельзя.

---

## 2. Забрать код

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
```

---

## 3. Собрать панель

Панель отдаётся статикой из каталога, который монтируется в Caddy.
Собранного каталога в репозитории нет (он в .gitignore) — собрать
локально.

```powershell
cd helm\tree\panel
npm ci
npm run build
cd ..\..\..
```

Должно закончиться строкой `✓ built`. Если `npm ci` ругается на
отсутствие package-lock.json — `npm install`.

---

## 4. Доставить файлы

```powershell
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\migrations helm@185.250.44.137:/tmp/migrations
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\panel\dist helm@185.250.44.137:/tmp/panel-dist
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\compose\docker-compose.yml helm@185.250.44.137:/tmp/docker-compose.yml
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\config\Caddyfile helm@185.250.44.137:/tmp/Caddyfile
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\restore_test.sh helm@185.250.44.137:/tmp/restore_test.sh
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\hermes\plugins\helm-control\__init__.py helm@185.250.44.137:/tmp/helm-control-init.py
```

Про `docker-compose.yml` и `Caddyfile` — их легко забыть, и прошлый раз
это уже стоило отладки: в compose объявлены **новые секреты**
Knowledge-бота, а в Caddy — **новый маршрут вебхука**. Без них код
приедет, но бот работать не будет: секреты прочитаются пустыми, а
вебхук снаружи упрётся в статику панели и получит 404.

Про плагин `helm-control` — он живёт на хосте у Hermes, а не в
`/opt/helm`, и `docker compose build` его не видит вообще.

---

## 5. Разложить по местам

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/control-plane/helm_core /opt/helm/control-plane/migrations && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo mv /tmp/migrations /opt/helm/control-plane/migrations && sudo mv /tmp/docker-compose.yml /opt/helm/compose/docker-compose.yml && sudo mv /tmp/Caddyfile /opt/helm/config/Caddyfile && sudo mv /tmp/restore_test.sh /opt/helm/scripts/restore_test.sh && sudo chown -R root:root /opt/helm/control-plane/helm_core /opt/helm/control-plane/migrations /opt/helm/compose/docker-compose.yml /opt/helm/config/Caddyfile /opt/helm/scripts/restore_test.sh && sudo chmod -R go-w /opt/helm/control-plane/helm_core /opt/helm/control-plane/migrations && sudo chmod 755 /opt/helm/scripts/restore_test.sh"
```

Панель — отдельной командой, и **содержимое каталога заменяется, сам
каталог не удаляется**:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/panel/dist/* && sudo cp -r /tmp/panel-dist/. /opt/helm/panel/dist/ && sudo rm -rf /tmp/panel-dist && sudo chown -R root:root /opt/helm/panel/dist"
```

Почему именно так: Caddy держит этот каталог примонтированным. Если
удалить сам каталог и создать заново, монтирование «протухнет» и Caddy
начнёт отдавать 404 на всё, пока его не перезапустишь. Это уже
случалось (F-260829-10).

---

## 6. Завести секреты Knowledge-бота

Делает **владелец**, значения агент не видит и не пересылает.

1. В BotFather: `/newbot`, имя на ваш вкус, получить токен.
2. Придумать секрет вебхука — **только буквы, цифры, `_` и `-`**.
   Не base64: MAX однажды отверг такой секрет и вернул его открытым
   текстом в теле ошибки (F-260829-20), с Telegram проверять это на
   себе незачем.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137
```

Дальше на сервере:

```bash
sudo sh -c 'printf %s "СЮДА_ТОКЕН_ОТ_BOTFATHER" > /etc/helm/secrets/knowledge_telegram_bot_token'
sudo sh -c 'printf %s "СЮДА_ПРИДУМАННЫЙ_СЕКРЕТ" > /etc/helm/secrets/knowledge_telegram_webhook_secret'
sudo chown root:helm-secrets /etc/helm/secrets/knowledge_telegram_bot_token /etc/helm/secrets/knowledge_telegram_webhook_secret
sudo chmod 640 /etc/helm/secrets/knowledge_telegram_bot_token /etc/helm/secrets/knowledge_telegram_webhook_secret
```

`printf %s`, а не `echo` — `echo` допишет перевод строки, и токен
уедет в Telegram с лишним символом на конце.

Владелец и группа именно такие: контейнер работает не под root и читает
секрет по группе (F-260829-09).

Имя бота (публичное, не секрет) — в `/opt/helm/compose/docker-compose.yml`,
строка `HELM_KNOWLEDGE_TELEGRAM_BOT_USERNAME`. Вписать без `@`. Без него
приглашение выдастся голым токеном вместо готовой ссылки.

---

## 7. Пересобрать и поднять

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose build helm-core helm-knowledge-worker && sudo docker compose up -d --force-recreate helm-core helm-knowledge-worker && sleep 20 && sudo docker compose ps helm-core helm-knowledge-worker"
```

Воркер пересобирается тоже: он собирается из того же каталога и держит
свою копию кода. Забыть его — значит получить воркер, который не знает
про владельцев записей и упадёт на первом же задании.

Caddy — отдельно, чтобы подхватил новый маршрут:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose restart caddy && sleep 5 && sudo docker compose logs --tail 20 caddy"
```

В логах не должно быть строк про ошибку разбора конфигурации.

---

## 8. Накатить миграции

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core python3 -m alembic current"
```

Ожидается `03af17f40250`. Дальше:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core python3 -m alembic upgrade head && sudo docker compose exec -T helm-core python3 -m alembic current"
```

Пройдут три штуки подряд:

| Миграция | Что делает |
|---|---|
| `f6617c6739ee` | Таблицы для загрузки ZIP пачкой |
| `ef1ba5467e14` | Владелец у каждой записи + проставление его всем существующим строкам |
| `4da8c9e90115` | Запрет на уровне самой базы (RLS) |

Ожидается `4da8c9e90115 (head)`.

Средняя миграция **не переразбирает и не перечанкует** существующие
документы — только проставляет им владельца. Если бы переразбирала,
выкат занял бы часы и сжёг бы CPU на Docling.

---

## 9. Сверить, что старое на месте

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc 'select count(*) from knowledge_sources' && sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc 'select count(*) from knowledge_chunks' && sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc 'select role, status, count(*) from knowledge_users group by 1,2'"
```

Числа документов и кусков — **ровно те же**, что в шаге 0. Владельцев
должно быть ровно один, `SYSTEM_OWNER` / `ACTIVE`.

Ни одной записи без владельца:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker exec helm-postgres-1 psql -U helm -d helm -tAc 'select count(*) from knowledge_sources where knowledge_user_id is null'"
```

Ожидается `0`.

---

## 10. Обновить плагин Telegram и перезапустить Hermes

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cp /tmp/helm-control-init.py ~/.hermes/plugins/helm-control/__init__.py && sudo systemctl restart hermes-gateway && sleep 5 && sudo systemctl status hermes-gateway --no-pager | head -15"
```

В плагине появился путь «Запомни»: он ловит команду **до** обычной
отправки чифу и до диалогов выбора домена. Важное свойство — если
Control Plane недоступен, сообщение **не** проваливается дальше к
модели, а честно отвечает «не запомнил». Иначе вы бы увидели вежливое
подтверждение от модели, а в базе не было бы ничего.

---

## 11. Зарегистрировать вебхук Knowledge-бота

На сервере, подставив свои значения:

```bash
TOKEN=$(sudo cat /etc/helm/secrets/knowledge_telegram_bot_token)
SECRET=$(sudo cat /etc/helm/secrets/knowledge_telegram_webhook_secret)
curl -sS -X POST "https://api.telegram.org/bot$TOKEN/setWebhook" \
  -d "url=https://helm.cmpas.ru/hooks/knowledge-telegram" \
  -d "secret_token=$SECRET" \
  -d "allowed_updates=[\"message\"]"
```

Ожидается `{"ok":true,...}`.

Если ответ не пришёл вовсе — сервер не достаёт до Telegram напрямую,
это уже известно (F-260830-03), исходящий трафик к Telegram пущен через
туннель. Тогда тот же запрос через прокси:

```bash
curl -sS --proxy http://127.0.0.1:18080 -X POST "https://api.telegram.org/bot$TOKEN/setWebhook" -d "url=https://helm.cmpas.ru/hooks/knowledge-telegram" -d "secret_token=$SECRET" -d "allowed_updates=[\"message\"]"
```

Проверить, что Telegram видит адрес:

```bash
curl -sS --proxy http://127.0.0.1:18080 "https://api.telegram.org/bot$TOKEN/getWebhookInfo"
```

---

## 12. Живая проверка

По порядку. Каждый пункт — отдельная возможность найти то, чего тесты
не видят.

**12.1. Ничего не сломалось.** Обычный вопрос в Telegram («какая погода
в Москве») уходит к модели как раньше. Вопрос, ответ на который есть в
базе, приходит мгновенно с указанием источника и без вызова модели.

**12.2. «Запомни».** Написать боту-чифу:

```
Запомни: номер машины курьера А123ВС77
```

Ожидается быстрый ответ «Запомнил: …». Затем спросить:

```
Напомни мне номер машины курьера
```

Ожидается **дословно тот же текст**, что вы сохранили.

**12.3. Секрет не сохраняется.** Написать `Запомни пароль от почты
хххх`. Ожидается отказ с советом про менеджер паролей, и в базе ничего
не появляется.

**12.4. Временный факт.** `Запомни: курьер приедет сегодня` — после
конца суток этот факт перестаёт находиться обычным вопросом, но
находится вопросом «какой был … вчера?».

**12.5. ZIP.** Отправить в MAX архив из нескольких документов. Ожидается
меню доменов, затем **одно** итоговое уведомление о завершении разбора,
а не по одному на каждый файл.

**12.6. Второй пользователь.** В панели: Система → Пользователи →
Пригласить. Скопировать ссылку, открыть **с другого Telegram-аккаунта**,
нажать «Запустить». Ожидается приветствие. Затем с того же аккаунта:
`Запомни: тестовая заметка` → сохранилось. Спросить у него что-нибудь
из вашей базы — **не должно найтись ничего вашего**.

**12.7. Чужой в вашего бота не проходит.** Тот же второй аккаунт пишет
вашему обычному боту-чифу — ответа быть не должно, allowlist не менялся.

**12.8. Панель второму человеку.** Система → Пользователи → Доступ в
панель. Токен передать человеку, он открывает
`https://helm.cmpas.ru/login?step=knowledge-enroll`, вводит токен,
создаёт passkey. Видит **только** свой Второй мозг — ни одобрений, ни
задач, ни денег, ни системы.

**12.9. Приостановка.** Система → Пользователи → Приостановить. У
человека немедленно перестаёт открываться панель (не через сутки), и бот
отвечает отказом.

---

## 13. Бэкап и проверка восстановления

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo /opt/helm/scripts/backup.sh && sudo /opt/helm/scripts/restore_test.sh"
```

В выводе проверки восстановления появились новые строки — про владельца,
вторых пользователей, отсутствие «осиротевших» записей и число
восстановленных зеркал памяти. Ожидается `RESTORE TEST PASSED`.

---

## Если что-то пошло не так

Откат кода и схемы:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core python3 -m alembic downgrade 03af17f40250"
```

Откат проверен на тестовой базе в обе стороны. Данные при этом
остаются: откат убирает колонки владельца и новые таблицы, но не трогает
сами документы и куски.

Если упало между шагами и состояние непонятно — восстановить из точки
возврата шага 0, это надёжнее, чем угадывать.

**Что записать в отчёт после выката** (пригодится для документов
передачи, §35): версии контейнеров, занятая память и диск, дата этого
бэкапа и проверки восстановления, какие пункты шага 12 прошли, а какие
нет.
