# Ранбук: развернуть HELM Knowledge — схема + Probe wiring (P8.5.1/4/5, v3.4)

**Порядок обязателен.** `backup.sh` теперь ссылается на
`/opt/helm-knowledge` — если применить обновлённый `backup.sh` раньше,
чем появится каталог, `restic` предупредит о недостающем пути и
завершится ненулевым кодом; `set -euo pipefail` оборвёт скрипт ДО
`restic forget` и до `touch last-backup`, и Guardian начнёт считать
бэкап устаревшим, хотя Postgres/Hermes на деле сохранились бы
нормально. Поэтому: сначала каталоги, потом обновлённый `backup.sh`, и
проверка бэкапа — в конце, не пропускать.

Эта редакция ранбука везёт ДВА коммита разом: схему БД (P8.5.1) и
wiring Probe (P8.5.4/5 частично — `/internal/knowledge/probe`,
`/hooks/max`, `helm-control`). Шаг 1 копирует `helm_core` целиком, так
что код обоих коммитов доставляется одной командой; отдельного
внимания требует только плагин `helm-control` (шаг 1b) — он живёт вне
`/opt/helm/control-plane`, на хосте у Hermes, и `docker compose build`
его не видит вообще.

## 1. Доставить код Control Plane и применить миграцию БД

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\migrations helm@185.250.44.137:/tmp/migrations
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\knowledge-bootstrap.sh helm\tree\scripts\backup.sh helm\tree\scripts\knowledge-probe-smoke-test.sh helm@185.250.44.137:/tmp/
```

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/control-plane/helm_core /opt/helm/control-plane/migrations && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo mv /tmp/migrations /opt/helm/control-plane/migrations && sudo chown -R root:root /opt/helm/control-plane/helm_core /opt/helm/control-plane/migrations && sudo chmod -R go-w /opt/helm/control-plane/helm_core /opt/helm/control-plane/migrations && cd /opt/helm/compose && sudo docker compose build helm-core && sudo docker compose up -d --force-recreate helm-core && sleep 15 && sudo docker compose ps helm-core"
```

Применить миграцию — внутри уже пересобранного контейнера, у него есть
и код, и переменная окружения с БД:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose exec -T helm-core python3 -m alembic upgrade head"
```

Ожидается `Running upgrade f12e419c664b -> b0ff2dca9936, helm knowledge tables`.
Проверить, что таблицы реально появились:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker exec helm-postgres-1 psql -U helm -d helm -c '\dt knowledge_*'"
```

Ожидается список из 6 строк (`knowledge_sources`, `knowledge_chunks`,
`knowledge_notes`, `knowledge_relations`, `knowledge_ingest_jobs`,
`knowledge_answer_runs`).

## 1b. Доставить плагин helm-control (Hermes на хосте, не контейнер)

Плагин живёт в `~/.hermes/plugins/helm-control/` под пользователем
`helm` — вне `/opt/helm/control-plane`, шаг 1 его не касается вовсе.
Изменился только `__init__.py` (добавлен вызов Probe), `plugin.yaml` не
трогали.

```powershell
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\hermes\plugins\helm-control\__init__.py helm@185.250.44.137:/tmp/helm-control-init.py
```

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cp /tmp/helm-control-init.py ~/.hermes/plugins/helm-control/__init__.py && sudo systemctl restart hermes-gateway && sleep 5 && sudo systemctl status hermes-gateway --no-pager | head -15"
```

Ожидается `active (running)`. Если нет — `journalctl -u hermes-gateway -n 50`.

## 2. Создать каталоги Vault (до обновления backup.sh)

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo bash /tmp/knowledge-bootstrap.sh"
```

Ожидается список из 16 каталогов под `/opt/helm-knowledge` + один
`/opt/helm-state/knowledge-spool`.

## 3. Обновить backup.sh — только теперь, не раньше

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo mv /tmp/backup.sh /opt/helm/scripts/backup.sh && sudo chown root:root /opt/helm/scripts/backup.sh && sudo chmod 755 /opt/helm/scripts/backup.sh"
```

## 4. Проверить, что бэкап всё ещё работает целиком

Обязательный шаг, не пропускать — единственный способ убедиться, что
новый путь в `backup.sh` не сломал ночной бэкап:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo /opt/helm/scripts/backup.sh && sudo /opt/helm/scripts/restore_test.sh"
```

Ожидается `BACKUP DONE` и `RESTORE TEST PASSED`, как и раньше.

## 5. Смоук-тест Probe целиком (схема + wiring, реальный Postgres/HTTP)

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo bash /tmp/knowledge-probe-smoke-test.sh"
```

Ожидается три `OK:` строки подряд (`NEEDS_REASONING`, затем после ingest
— `LOCAL_ANSWER, источник процитирован`) и `DONE` в конце. Любой
`AssertionError` — вставить весь вывод в чат, разбираем по факту, не по
предположению. Тестовая запись (`deploy-smoke-test.md`, домен
`engineering`) остаётся в базе — безопасно, помечена как смоук-тест,
удалять не обязательно.

## 6. Живая проверка на реальном сообщении (после смоук-теста)

Не автоматизируется скриптом — нужен реальный чат:

1. **MAX**: отправь боту вопрос, пересекающийся с тестовой записью
   (например «какой пробный факт после деплоя»). Ожидается мгновенный
   ответ с текстом записи и `deploy-smoke-test.md` — chief-агент НЕ
   вызывается (можно проверить `sudo docker compose logs helm-core
   --since 2m | grep -i hermes` — вызова быть не должно). Затем задай
   обычный вопрос не по теме — ожидается прежнее поведение (ответ от
   chief через Hermes, как до этого деплоя) — регрессия на всём
   остальном трафике недопустима.
2. **Telegram**: то же самое через `helm-control`. Если Probe недоступен
   или падает — сообщение по конструкции идёт к LLM как обычно
   (fail-open), это нормально для Probe, но означает, что бесплатный
   путь не сработал; смотреть `journalctl -u hermes-gateway -n 50` на
   `[helm-control] knowledge_probe failed: ...`, если ответ пришёл от
   chief там, где ожидался LOCAL_ANSWER.
