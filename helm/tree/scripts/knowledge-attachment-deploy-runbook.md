# Ранбук: развернуть P8.5.7 (вложения Telegram/MAX, двухшаговый диалог)

Что здесь реально ново и непроверено вживую (всё остальное — 20 новых
тестов против настоящего Postgres через `TestClient`, `191/191` зелёных
локально, `docs/KNOWLEDGE_INGEST.md`):

- alembic-миграция `1fcf9edaca25` (таблица `knowledge_pending_attachments`);
- `docker-compose.yml`: `helm-core` получил два новых volume mount'а
  (`/opt/helm-state/knowledge-spool`, `/opt/helm-knowledge`) и `group_add:
  ["1001"]` — то, что прошлый раз (P8.5.2) потребовало реальной живой
  правки прав, здесь сделано по тому же паттерну ЗАРАНЕЕ, но не
  проверено на реальном контейнере;
- `knowledge-bootstrap.sh`: spool теперь `770 + setgid`, было `700`.

Реальная форма вложения MAX (`message.body.attachments`) — **не**
проверена: `dev.max.ru` недоступен из egress-политики песочницы
разработки. Код (`helm_core/channels/max.py::parse_attachment`) написан
по документированному поведению TamTam-производных Bot API и явно
логирует расхождение (`MaxAttachmentUnsupported` с именами полей, не
значениями), если реальный вебхук не совпадёт — это ОТДЕЛЬНАЯ, более
дешёвая проверка (шаг 3 ниже), не блокирует остальной деплой.

## 0. Доставить код

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\migrations helm@185.250.44.137:/tmp/migrations
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\compose\docker-compose.yml helm@185.250.44.137:/tmp/
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\knowledge-bootstrap.sh helm@185.250.44.137:/tmp/
```

Дождись, пока эта команда закончится и появится приглашение
`PS D:\...>`, и только потом отправляй следующую.

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/control-plane/helm_core && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo chown -R root:root /opt/helm/control-plane/helm_core && sudo rm -rf /opt/helm/control-plane/migrations && sudo mv /tmp/migrations /opt/helm/control-plane/migrations && sudo chown -R root:root /opt/helm/control-plane/migrations && sudo mv /tmp/docker-compose.yml /opt/helm/compose/docker-compose.yml && sudo chown root:root /opt/helm/compose/docker-compose.yml"
```

## 1. Применить миграцию

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core alembic upgrade head"
```

Ожидается `... -> 1fcf9edaca25, knowledge pending attachments`.

## 2. Обновить права spool и пересоздать helm-core с новыми mount'ами

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo bash /tmp/knowledge-bootstrap.sh"
```

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose up -d helm-core && sleep 5 && sudo docker compose ps helm-core"
```

Проверка того, что реально было непроверено (права/mount'ы, не бизнес-логика
— она уже проверена 191 тестом локально):

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core sh -c 'touch /opt/helm-state/knowledge-spool/smoke.tmp && touch /opt/helm-knowledge/raw/engineering/smoke.tmp && echo OK: helm-core пишет в оба каталога && rm /opt/helm-state/knowledge-spool/smoke.tmp /opt/helm-knowledge/raw/engineering/smoke.tmp'"
```

**Если `Permission denied`** — вставь ошибку в чат целиком. Вероятная
причина по опыту P8.5.2: `knowledge-bootstrap.sh` не был перезапущен на
сервере ДО пересоздания контейнера, или GID `1001` внутри контейнера не
совпадает с хостовым (`getent group helm` на хосте против `id` внутри
контейнера — `sudo docker compose exec -T helm-core id`).

## 3. Живая проверка реальной формы MAX-вложения (когда удобно, не блокирует остальное)

Отправь любой файл владельцу-боту в MAX. Дальше зависит от результата:

- Пришёл ответ с меню доменов (`Файл «...» получен и сохранён. В какой
  домен положить?`) — форма подтвердилась, реальная эскалация работает,
  можно закрывать P8.5.7 (MAX-сторону) как проверенную живьём.
- Пришло «Не смог скачать вложение» — это ожидаемый, задокументированный
  исход (см. предупреждение выше). Смотри лог:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker compose -f /opt/helm/compose/docker-compose.yml logs helm-core --tail 50 | grep 'не удалось скачать'"
```

Строка лога несёт `MaxAttachmentUnsupported: неизвестная форма payload
для типа='...', ключи=[...]` — это ИМЕНА полей реального payload
(значения, включая любой токен, туда никогда не попадают). Пришли эту
строку в чат целиком — правка `parse_attachment()` под реальную форму
после этого — один точечный патч, не новый цикл гадания.

## 4. Telegram-сторона — отдельная, более ранняя развилка

Прежде чем что-либо писать для Telegram, нужна разведка возможностей
самого Hermes-хука (ADR-018 явно это допускает как легитимный исход).
Read-only, ничего не меняет:

```powershell
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\knowledge-telegram-attachment-recon.sh helm@185.250.44.137:/tmp/
```
```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "bash /tmp/knowledge-telegram-attachment-recon.sh"
```

Пришли весь вывод целиком — по нему пишется код для Telegram-стороны
(либо штатный путь через event, либо "smallest transport adapter" по
ADR-018, если штатного нет).
