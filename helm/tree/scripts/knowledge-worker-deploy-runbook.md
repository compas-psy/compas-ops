# Ранбук: развернуть HELM Knowledge async worker (P8.5.2)

Первая живая проверка того, что нельзя было проверить в песочнице
разработки: сборка `Dockerfile.worker` (Docker Hub недоступен из
песочницы) и реальная эскалация MarkItDown→Docling (huggingface.co для
моделей Docling тоже недоступен). Ожидай, что здесь что-то пойдёт не
так с первого раза — это и есть цель ранбука, не формальность.

## 0. Доставить код и fixture-файлы для смоук-теста

```powershell
cd D:\ПРОЕКТЫ\simpas\helm\compas-ops
git pull origin claude/ai-agents-server-deployment-xdp77a
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" -r helm\tree\control-plane\helm_core helm@185.250.44.137:/tmp/helm_core
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\control-plane\Dockerfile.worker helm@185.250.44.137:/tmp/
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\compose\docker-compose.yml helm@185.250.44.137:/tmp/
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\knowledge-worker-smoke-test.sh helm@185.250.44.137:/tmp/
```

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "mkdir -p /tmp/knowledge-worker-fixtures"
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\control-plane\tests\fixtures\knowledge\sample.docx helm\tree\control-plane\tests\fixtures\knowledge\sample_broken_font.pdf helm@185.250.44.137:/tmp/knowledge-worker-fixtures/
```

## 1. Обновить код Control Plane, compose-файл и собрать образ воркера

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo rm -rf /opt/helm/control-plane/helm_core && sudo mv /tmp/helm_core /opt/helm/control-plane/helm_core && sudo chown -R root:root /opt/helm/control-plane/helm_core && sudo chmod -R go-w /opt/helm/control-plane/helm_core && sudo mv /tmp/Dockerfile.worker /opt/helm/control-plane/Dockerfile.worker && sudo chown root:root /opt/helm/control-plane/Dockerfile.worker && sudo mv /tmp/docker-compose.yml /opt/helm/compose/docker-compose.yml && sudo chown root:root /opt/helm/compose/docker-compose.yml"
```

Сборка образа — единственный шаг, который тянет ~5.7GB зависимостей
(torch, OCR-модели Docling) — рассчитывай на несколько минут, не
секунд:

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose build helm-knowledge-worker"
```

**Если сборка падает** — это ожидаемо возможно (не проверено вживую
никогда): вставь ошибку в чат целиком, разбираем по факту, не по
предположению. Вероятные места: `pip install torch --index-url
https://download.pytorch.org/whl/cpu` (доступность индекса с сервера),
недостающий apt-пакет для opencv/Docling помимо уже перечисленных
(`libgl1 libglib2.0-0 libsm6 libxext6 libxrender1`).

## 2. Проверить, что образ не притащил CUDA по ошибке

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker run --rm helm-knowledge-worker:0.1.0 python3 -c 'import torch; print(torch.__version__)'"
```

Ожидается версия БЕЗ суффикса `+cu1XX` (например `2.9.0+cpu`). Суффикс
`+cu` означает, что CUDA-сборка всё же попала в образ — лишние ~1.2GB,
проверить `RUN pip install torch --index-url ...` в Dockerfile.worker
сработал ли вообще (возможно кэш слоя от более ранней неудачной сборки).

## 3. Поднять контейнер

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "cd /opt/helm/compose && sudo docker compose up -d helm-knowledge-worker && sleep 5 && sudo docker compose ps helm-knowledge-worker && sudo docker compose logs helm-knowledge-worker --tail 20"
```

Ожидается `knowledge ingest worker started` в логе, контейнер `Up`
(healthcheck не настроен — это не HTTP-сервис).

## 4. Живая проверка: реальный разбор + реальная эскалация на Docling

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo bash /tmp/knowledge-worker-smoke-test.sh"
```

Ожидается: `smoke-test.docx` → `parser=markitdown`, `job_status=DONE`,
L1 SOURCE файл существует и содержит текст документа.
`smoke-test-broken.pdf` — **самое важное**: MarkItDown должен провалить
quality gate (испорченный текст из-за шрифта без кириллицы), роутер
обязан попытаться эскалировать на Docling — это первая живая проверка,
что Docling реально скачивает модели с huggingface.co и работает (или
не работает — тогда вставь полный текст ошибки/`job.error` в чат).

## 5. Ресурсы после реального использования

```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "sudo docker stats --no-stream helm-knowledge-worker-1 && df -h /"
```

Сравни с `knowledge-parsers-preflight.sh` (до установки) — если RSS
контейнера после разбора одного PDF уже близок к лимиту `3g`, лимит в
`docker-compose.yml` придётся поднять по факту измерения, не гадать
заранее.

## 6. Очистка тестовых данных

```powershell
scp -i "C:\Users\eliah\.ssh\helm_deploy_key" helm\tree\scripts\knowledge-worker-smoke-test-cleanup.sh helm@185.250.44.137:/tmp/
```
```powershell
ssh -i "C:\Users\eliah\.ssh\helm_deploy_key" helm@185.250.44.137 "bash /tmp/knowledge-worker-smoke-test-cleanup.sh"
```
