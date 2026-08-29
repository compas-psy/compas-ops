# Ранбук: развернуть схему HELM Knowledge (P8.5.1, v3.4)

**Порядок обязателен.** `backup.sh` теперь ссылается на
`/opt/helm-knowledge` — если применить обновлённый `backup.sh` раньше,
чем появится каталог, `restic` предупредит о недостающем пути и
завершится ненулевым кодом; `set -euo pipefail` оборвёт скрипт ДО
`restic forget` и до `touch last-backup`, и Guardian начнёт считать
бэкап устаревшим, хотя Postgres/Hermes на деле сохранились бы
нормально. Поэтому: сначала каталоги, потом обновлённый `backup.sh`, и
проверка бэкапа — в конце, не пропускать.

## 1. Доставить код и применить миграцию БД

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\migrations helm@185.250.44.137:/tmp/migrations
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\knowledge-bootstrap.sh helm\tree\scripts\backup.sh helm@185.250.44.137:/tmp/
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
