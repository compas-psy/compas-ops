#!/bin/bash
# Живая проверка E12 фазы 2 (Z2-рефраз gemma2:2b + личный стиль владельца)
# после выката. Тот же факт/вопрос, что в живом замере моделей
# (docs/KNOWLEDGE_MODELS.md, «Живой замер 31.08.2026»). Read-only: не
# пишет и не читает базу, звонит только в Ollama.
# Запускается на сервере: bash /tmp/recon.sh
set -uo pipefail
cd /opt/helm/compose || exit 1

# НАЙДЕНО живым прогоном R4 02.09.2026: этот же текст/вопрос через
# probe() перестал что-либо доказывать про Z2 после решения владельца
# 01.09.2026 сделать общий поиск глобальным по корпусу (probe.py:125-142,
# health включён) — probe.py:397 зовёт rephrase_or_none() только при
# mode=="Z0" (ровно одна evidence-запись), а против реального корпуса
# (953+ health-чанков) этот вопрос предсказуемо цепляет несколько
# посторонних совпадений → mode=Z1 → рефраз не вызывается вообще,
# независимо от здоровья gemma2:2b. Прямой вызов rephrase() — единственная
# честная проверка самой модели; проверку её вписывания в probe() со
# стилем нужно делать отдельно, на изолированном домене/тенанте, не здесь.
echo '=== Z2-рефраз: прямой вызов rephrase() со стилем владельца ==='
sudo docker compose exec -T helm-core python3 <<'PY'
from helm_core.knowledge.rephrase import rephrase, RephraseUnavailable
from helm_core.knowledge.style import OWNER_STYLE_VERSION, style_prompt_for_version

try:
    text = rephrase(
        "что такое схема?",
        "Схема — это устойчивый паттерн мышления и поведения, сформированный в детстве.",
        system_prompt=style_prompt_for_version(OWNER_STYLE_VERSION),
    )
    print("Z2_DIRECT: OK")
    print("answer_text:")
    print(text)
except RephraseUnavailable as exc:
    print("Z2_DIRECT: FAIL", repr(str(exc)))
PY

echo
echo '=== ollama: резидентная память после вызова (KEEP_ALIVE=0 должен выгрузить веса) ==='
sudo docker stats --no-stream --format '{{.Name}}: {{.MemUsage}}' "$(sudo docker compose ps -q ollama)"
