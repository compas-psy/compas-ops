# Ранбук: развернуть Telegram-сторону P8.5.7 (вложения через helm-control)

Что здесь ново и не проверено вживую (бизнес-логика уже покрыта 210
тестами локально, `docs/KNOWLEDGE_INGEST.md`):

- два новых internal-эндпоинта в Control Plane (`/internal/knowledge/
  attachment/stage`, `/internal/knowledge/attachment/resolve`) —
  `helm_core/api/internal.py`;
- `helm-control/__init__.py` (плагин Hermes, вне Docker, вне pytest):
  перехват вложения до проверки `event.text`, скачивание через
  `event.raw_message.get_file()`, вызов новых эндпоинтов.

Новой alembic-миграции в этом деплое нет — колонка
`KnowledgeIngestJob.recipient` уже накатана прошлым циклом (3-шаговые
уведомления). Новый секрет тоже не нужен — оба эндпоинта подписываются
тем же `hermes_service_hmac`, что уже использует `/internal/knowledge/
probe`.

## 0. Доставить код

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\hermes\plugins\helm-control\__init__.py helm@185.250.44.137:/tmp/helm-control-init.py
```

Дождись, пока эта команда закончится и появится приглашение
`PS D:\...>`, и только потом отправляй следующую.

## 1. Переложить helm_core на место и пересобрать helm-core

**Порядок важен**: сначала переложить код, потом `docker compose build`
— иначе сборка возьмёт старые закэшированные слои и молча соберёт
неизменившийся образ (дважды наступали на это раньше в этом цикле).

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/control-plane/helm_core && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo chown -R root:root /opt/helm/control-plane/helm_core && sudo chmod -R go-w /opt/helm/control-plane/helm_core && cd /opt/helm/compose && sudo docker compose build helm-core && sudo docker compose up -d --force-recreate helm-core && sleep 15 && sudo docker compose ps helm-core"
```

Ожидается `Up` для `helm-core`. Проверить, что новый код реально внутри
образа (не доверять одному только `ps`):

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core grep -n 'attachment/stage' /opt/helm/control-plane/helm_core/api/internal.py"
```

Ожидается строка с путём `/knowledge/attachment/stage`. Пусто —
значит образ не пересобрался, вставь в чат вывод шага 1 целиком.

## 2. Доставить плагин helm-control и перезапустить hermes-gateway

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cp /tmp/helm-control-init.py ~/.hermes/plugins/helm-control/__init__.py && sudo systemctl restart hermes-gateway && sleep 5 && sudo systemctl status hermes-gateway --no-pager | head -15"
```

Ожидается `active (running)`. Если нет — пришли вывод
`journalctl -u hermes-gateway -n 50`.

## 3. Живая проверка

Не автоматизируется скриптом — нужен реальный Telegram-чат с ботом.

1. Пришли боту любой файл (документ или фото) **без** предварительного
   текстового вопроса. Ожидается: в ответ приходит меню доменов (то же,
   что уже видел в MAX) — значит `_message_has_attachment` сработал
   раньше проверки `event.text`, скачивание и `stage_attachment` прошли.
2. Ответь номером/именем/алиасом домена (например `health` или
   `company`). Ожидается: подтверждение `Сохранено в «...». Разбор
   запущен...`, затем (в фоне, воркер) — уведомление о завершении
   разбора. Три сообщения подряд, как уже подтверждено на MAX.
3. **Критическая проверка** (решение владельца 30.08.2026): чиф НЕ
   должен обработать вложение агентно — не должно быть его обычного
   OCR/shell-разбора файла, только автоматический ответ меню доменов.
   Проверить логами, что chief не вызывался на это сообщение:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo journalctl -u hermes-gateway --since '5 min ago' | grep -i 'knowledge_attachment\|hermes\|chief'"
```

4. Если что-то пошло не так — первым делом:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo journalctl -u hermes-gateway -n 100 --no-pager"
```

Строка `[helm-control] не удалось скачать вложение: ...` или
`[helm-control] stage_attachment failed: ...` — вставь целиком в чат,
разбираем по факту.
