#!/bin/bash
# HELM v4.0 RESCUE · R4.6.F1.2 (владелец 03.09.2026) — NLI relation
# benchmark на ЗАМОРОЖЕННОМ v3 dataset (`relation_benchmark_v3_fixtures.py`,
# freeze commit f8e32a576297d04c90b3bfb4fd2fdf7f1d1c4eb7): 95 positives +
# 190 negatives, 20 hand-written кейсов, quoted-reference verbalizer v3
# (не родовая ссылка v2, не canonical_text-как-именная-группа v1).
#
# Отличие методологии от R4.6.F1d (`r4-f1-nli-benchmark.sh`, LOOCV по
# всем 15 golden-кейсам сразу): здесь есть ЯВНЫЙ frozen split по `case_id`
# — 16 calibration-кейсов (78 positives) / 4 final_holdout-кейса (17
# positives). Порядок:
#   1. LOOCV ВНУТРИ calibration (16 фолдов) — санity-check, что порог
#      вообще стабильно достижим на calibration-данных.
#   2. Один финальный threshold, подобранный на ВСЕХ calibration-примерах
#      разом (max recall при precision >= 0.90).
#   3. Этот порог применяется РОВНО ОДИН РАЗ к final_holdout — это и есть
#      отчётные метрики продуктового гейта. Holdout НЕ участвует ни в
#      LOOCV, ни в подборе финального порога.
#   4. AUROC/AUPRC — отдельно на calibration и на final_holdout
#      (threshold-независимые, но holdout-версия — честная оценка
#      generalization, calibration-версия — informational).
#
# Тот же lifecycle-контракт, что во всех предыдущих R4.6 диагностических
# скриптах: set -Eeuo pipefail, ORIGINAL_MEMORY/MEMORY_SWAP раздельно,
# idempotent cleanup() через trap EXIT/INT/TERM, PRE/POST verification,
# sha256 исполняемого файла.
#
# НАЙДЕНО живым прогоном (run 234, ModuleNotFoundError): helm-knowledge-
# worker — СОБРАННЫЙ образ (`COPY helm_core` в Dockerfile.worker, не
# bind-mount с хоста), новый код (verbalizer v3/fixtures v3/dataset
# builder v3) физически отсутствует внутри контейнера до полноценного
# `action=deploy` — а деплоить агенту запрещено (CLAUDE.md §5.2).
# `recon`-пайплайн переносит на VPS ровно один файл (сам `.sh`), поэтому
# Python-блок ниже — НЕ ручной код, а СБОРКА
# `scripts/r4-f1-2-build-recon-script.py` из трёх модулей
# `control-plane/helm_core/knowledge/{relation_verbalizer_v3,
# relation_benchmark_v3_fixtures,nli_relation_dataset_v3}.py` (реальный
# источник истины — там; здесь только относительные импорты переписаны
# на прямые ссылки). Сверено байт-в-байт: `build_examples_v3()` из
# собранного блока даёт те же 285 примеров, что и настоящий пакет — 0
# расхождений. Правки вносить в исходные `.py`, затем пересобирать этим
# генератором, не редактировать heredoc ниже вручную.
set -Eeuo pipefail
cd /opt/helm/compose

WORKER_CID() { sudo docker compose ps -q helm-knowledge-worker; }

echo "=== sha256 исполняемого скрипта ==="
sha256sum "${BASH_SOURCE[0]:-$0}" || true

CID="$(WORKER_CID)"
if [ -z "$CID" ]; then
  echo "::error::контейнер helm-knowledge-worker не найден — не продолжаем"
  exit 1
fi

echo
echo "=== PRE: состояние helm-knowledge-worker до изменений ==="
sudo docker inspect -f '{{.State.Status}}' "$CID"
sudo docker inspect -f '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}}' "$CID"
sudo docker stats --no-stream "$CID"
df -h / | tail -1
free -h | head -2

ORIGINAL_MEMORY=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$CID")
ORIGINAL_MEMORY_SWAP=$(sudo docker inspect -f '{{.HostConfig.MemorySwap}}' "$CID")
if [ -z "$ORIGINAL_MEMORY" ] || [ -z "$ORIGINAL_MEMORY_SWAP" ]; then
  echo "::error::не удалось прочитать исходные HostConfig.Memory/MemorySwap — не продолжаем"
  exit 1
fi

CLEANUP_DONE=0
cleanup() {
  local rc=$?
  if [ "$CLEANUP_DONE" -eq 1 ]; then
    exit "$rc"
  fi
  CLEANUP_DONE=1

  echo
  echo "############ CLEANUP (idempotent; исходный код завершения: $rc) ############"
  local cid post_memory post_swap
  cid="$(WORKER_CID)"
  if [ -z "$cid" ]; then
    echo "::error::контейнер helm-knowledge-worker не найден на этапе cleanup"
    exit "$rc"
  fi

  sudo docker update --memory="$ORIGINAL_MEMORY" --memory-swap="$ORIGINAL_MEMORY_SWAP" "$cid" >/dev/null 2>&1 \
    || echo "::error::не удалось восстановить memory limit — проверьте helm-knowledge-worker вручную"

  echo "=== POST: состояние helm-knowledge-worker после cleanup ==="
  sudo docker inspect -f '{{.State.Status}}' "$cid" || true
  post_memory=$(sudo docker inspect -f '{{.HostConfig.Memory}}' "$cid" 2>/dev/null || echo "?")
  post_swap=$(sudo docker inspect -f '{{.HostConfig.MemorySwap}}' "$cid" 2>/dev/null || echo "?")
  echo "$post_memory $post_swap"
  if [ "$post_memory" = "$ORIGINAL_MEMORY" ] && [ "$post_swap" = "$ORIGINAL_MEMORY_SWAP" ]; then
    echo "POST совпадает с PRE: Memory=$ORIGINAL_MEMORY MemorySwap=$ORIGINAL_MEMORY_SWAP"
  else
    echo "::error::POST НЕ совпадает с PRE — было Memory=$ORIGINAL_MEMORY/MemorySwap=$ORIGINAL_MEMORY_SWAP, стало Memory=$post_memory/MemorySwap=$post_swap"
  fi

  exit "$rc"
}
trap cleanup EXIT INT TERM

echo
echo "=== временно поднимаем лимит helm-knowledge-worker до 6g (постоянный лимит: 3g) ==="
sudo docker update --memory=6g --memory-swap=6g "$CID"

diag_rc=0
sudo docker compose exec -T helm-knowledge-worker python3 - <<'PYEOF' || diag_rc=$?
import resource
import time

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

# ---- склеено из helm_core/knowledge/relation_verbalizer_v3.py (единственный источник истины — см. control-plane/, не копия здесь) ----
"""R4.6.F1.2 (владелец 03.09.2026) — `RelationVerbalizerV2` устранял
подстановку `canonical_text` как именной группы, но заменил её на
РОДОВУЮ ссылку («описанное событие»), которая неоднозначна, если в
кейсе больше одного атома того же `kind` — обнаружено живым прогоном
R4.6.F1.1 audit: `long_dense_window` (7 атомов, два `event`) и
`typed_relations_variety` (4 атома, все `fact`) полностью выпали из v2
именно по этой причине, а не потому что для них не было verbalizer'а.

Этот модуль заменяет родовую ссылку на ДЕТЕРМИНИРОВАННУЮ quoted
reference: kind-noun (склоняемый по нужному падежу, та же таблица форм,
что в v2) + дословная цитата `canonical_text` в кавычках, например
`событие «20 января 2026 года состоялось совещание...»`,
`факт «тестирование проекта не завершено»`, `решение «перенести запуск
проекта на октябрь»`. Цитата уникальна для каждого атома внутри кейса
по построению (два разных атома не имеют идентичного `canonical_text`)
— guard на неоднозначность («ambiguous atom-kind reference»,
центральный механизм v2) здесь БОЛЬШЕ НЕ НУЖЕН и не воспроизводится:
задача, которую он решал, устранена на уровне verbalizer'а, а не
дополнительной проверкой вызывающей стороны.

Реестр `(relation_type, source_category:kind, target_category:kind)`
расширен относительно v2 до полного контракта онтологии всех 15 типов
(`docs/R4.6.F1.2-RELATION-ONTOLOGY.md`) — включая 8 типов, отсутствующих
в существующих golden fixtures (`has_role`, `part_of`, `created_by`,
`owned_by`, `contradicts`, `supersedes`, `derived_from`, `refers_to`):
для них нет исторических примеров для сверки, поэтому verbalizer здесь
и есть единственный источник контракта — сверяется вручную с
ontology-документом, не с живыми данными.

Domain-agnostic, никакого LLM, никакой эвристики словоизменения имён:
где нужна была бы декленация ПРОИЗВОЛЬНОГО `ENTITY.label` (падеж,
род/число глагола), функция вместо этого либо ставит сущность в позицию
именительного падежа (подлежащее или предикатив после тире — «Автор
{X} — {label}»), либо оборачивает `label` в кавычки после уже
просклонённого нарицательного существительного («роль «{label}»»,
«теме {label}») — та же техника, что в v2 не даёт вложенных кавычек и
не требует знать род/склонение имени."""


from dataclasses import dataclass
from typing import Literal

#: Владелец: `related_to` явно симметричен; `contradicts` — логически
#: симметричен (A противоречит B ⇔ B противоречит A). Полный контракт —
#: `docs/R4.6.F1.2-RELATION-ONTOLOGY.md` таблица направленности.
SYMMETRIC_RELATION_TYPES: frozenset[str] = frozenset({"related_to", "contradicts"})

UNSUPPORTED_FOR_NLI = "UNSUPPORTED_FOR_NLI"

NodeCategory = Literal["ENTITY", "ATOM"]

CaseForm = Literal["nom", "prep", "gen", "dat", "ins"]

#: Полная падежная парадигма на каждый ATOM.kind, плюс формы согласования
#: глагола/причастия по роду для тех verbalizer'ов, где атом — подлежащее
#: (`located_at`: "произошло/произошёл"; `resulted_in`: "привело/привёл";
#: `derived_from`: "основано/основан"). Собрано вручную, не эвристикой —
#: новый падёж/kind добавляется явной записью.
_KIND_FORMS: dict[str, dict[CaseForm, str]] = {
    "event": {"nom": "событие", "prep": "событии", "gen": "события",
             "dat": "событию", "ins": "событием"},
    "fact": {"nom": "факт", "prep": "факте", "gen": "факта",
            "dat": "факту", "ins": "фактом"},
    "decision": {"nom": "решение", "prep": "решении", "gen": "решения",
                "dat": "решению", "ins": "решением"},
    "concept": {"nom": "понятие", "prep": "понятии", "gen": "понятия",
               "dat": "понятию", "ins": "понятием"},
}

_PAST_AGREE = {"event": "произошло", "fact": "произошёл", "decision": "произошло"}
_LED_TO_AGREE = {"event": "привело", "fact": "привёл", "decision": "привело"}
_BASED_ON_AGREE = {"event": "основано", "fact": "основан", "decision": "основано"}

#: Дательный падеж существительного-темы для `about` (владелец: топик —
#: не только CONCEPT/ORGANIZATION; PLACE как топик — «относится к
#: месту X», дательный от «место» — «месту», НЕ «месте» (это
#: предложный) — поймано на этапе написания, не живым прогоном.
_ABOUT_TOPIC_NOUN = {"CONCEPT": "теме", "ORGANIZATION": "организации", "PLACE": "месту"}


@dataclass(frozen=True)
class Node:
    """`category="ENTITY"`: `ref_kind` — entity_type (PERSON/ORGANIZATION/
    PLACE/CONCEPT), `label` — реальная именная группа (используется как
    есть, без декленации). `category="ATOM"`: `ref_kind` — kind
    (event/fact/decision/concept), `label` — `canonical_text` атома
    (В ОТЛИЧИЕ ОТ v2 — здесь используется, это и есть quoted reference,
    не родовая замена)."""

    category: NodeCategory
    ref_kind: str
    label: str = ""


def _entity_label(node: Node) -> str:
    return node.label


def _quoted(node: Node, case: CaseForm) -> str:
    """`<падежная форма kind-noun> «<canonical_text>»` — цитата не
    склоняется (как и любая кавычная цитата/название в русском:
    ср. «в фильме «Летят журавли»» — «фильм» склоняется, заголовок
    внутри кавычек — нет)."""
    return f"{_KIND_FORMS[node.ref_kind][case]} «{node.label}»"


def _quoted_cap(node: Node, case: CaseForm) -> str:
    text = _quoted(node, case)
    return text[0].upper() + text[1:]


def _verbalize_involves(a: Node, b: Node) -> str:
    if b.ref_kind not in ("PERSON", "ORGANIZATION", "PLACE"):
        return UNSUPPORTED_FOR_NLI
    # Направление ПЕРЕСТАВЛЕНО относительно naive from/to (§14.9 gloss —
    # «атом вовлекает участника»): участник (b) — грамматический субъект.
    return f"{_entity_label(b)} участвует в {_quoted(a, 'prep')}."


def _verbalize_has_role(a: Node, b: Node) -> str:
    if a.ref_kind not in ("PERSON", "ORGANIZATION") or b.ref_kind != "CONCEPT":
        return UNSUPPORTED_FOR_NLI
    # `label_a` — подлежащее (именительный, декленация не нужна);
    # `label_b` в кавычках как обозначение роли — тоже без декленации.
    return f"{_entity_label(a)} занимает роль «{_entity_label(b)}»."


def _verbalize_about(a: Node, b: Node) -> str:
    # Найдено при написании v3 fixtures: событие тоже может иметь тему
    # («лекция была посвящена теме X») — исключение EVENT было излишне
    # узким (ontology contract исправлен вслед за этим).
    if a.ref_kind not in ("event", "concept", "fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    noun = _ABOUT_TOPIC_NOUN.get(b.ref_kind)
    if noun is None:
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} относится к {noun} {_entity_label(b)}."


def _verbalize_located_at(a: Node, b: Node) -> str:
    if a.ref_kind not in ("event", "fact"):
        return UNSUPPORTED_FOR_NLI
    if b.ref_kind not in ("PLACE", "ORGANIZATION"):
        return UNSUPPORTED_FOR_NLI
    # PLACE-лейбл без собственных кавычек (топоним вроде «Казань») нельзя
    # склонить в предложный падеж без словаря — найдено живым прогоном
    # recovery-check («в Казань» вместо «в Казани»). Классификатор «месте»
    # (уже в предложном падеже) перед лейблом снимает необходимость
    # склонения самого имени — тот же приём, что в `_verbalize_about`.
    # ORGANIZATION-лейблы в fixtures уже несут собственные кавычки/
    # классификатор («кафе «Пушкинъ»», «магазин «Ситилинк»») — классификатор
    # не добавляется, иначе получится двойной («в организации «Ситилинк»»
    # при уже квалифицированном лейбле — не ошибка, но избыточно).
    # Лейбл уже несёт собственный классификатор/кавычки («кафе «Пушкинъ»»)
    # — добавлять «месте» было бы избыточно (не ошибка, но лишнее).
    prefix = "месте " if b.ref_kind == "PLACE" and "«" not in b.label else ""
    return f"{_quoted_cap(a, 'nom')} {_PAST_AGREE[a.ref_kind]} в {prefix}{_entity_label(b)}."


def _verbalize_part_of(a: Node, b: Node) -> str:
    # Ограничено ORGANIZATION-ORGANIZATION (подразделение — часть
    # организации): PLACE-PLACE потребовал бы склонения произвольного
    # топонима в родительном падеже («в состав <?>») — недоступно без
    # словаря/эвристики имени, см. модуль docstring.
    if a.ref_kind != "ORGANIZATION" or b.ref_kind != "ORGANIZATION":
        return UNSUPPORTED_FOR_NLI
    return f"{_entity_label(a)} входит в состав организации {_entity_label(b)}."


def _verbalize_created_by(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("PERSON", "ORGANIZATION"):
        return UNSUPPORTED_FOR_NLI
    # Предикатив через тире вместо «создано {b}» (творительный падёж
    # произвольного имени недоступен без декленации) — `label_b` в
    # именительном, декленация не нужна.
    return f"Автор {_quoted(a, 'gen')} — {_entity_label(b)}."


def _verbalize_owned_by(a: Node, b: Node) -> str:
    if a.ref_kind != "fact" or b.ref_kind not in ("PERSON", "ORGANIZATION"):
        return UNSUPPORTED_FOR_NLI
    return f"Владелец {_quoted(a, 'gen')} — {_entity_label(b)}."


def _verbalize_resulted_in(a: Node, b: Node) -> str:
    if a.ref_kind not in ("event", "fact", "decision") or b.ref_kind not in ("event", "fact"):
        return UNSUPPORTED_FOR_NLI
    # v2 использовал «произошёл к» (бессмысленно для causation) —
    # исправлено на семантически верное «привело/привёл к» с
    # согласованием по роду источника (найдено при пересборке под
    # R4.6.F1.2, не живым прогоном).
    return f"{_quoted_cap(a, 'nom')} {_LED_TO_AGREE[a.ref_kind]} к {_quoted(b, 'dat')}."


def _verbalize_reason_for(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "event") or b.ref_kind != "decision":
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} — причина {_quoted(b, 'gen')}."


def _verbalize_supports(a: Node, b: Node) -> str:
    if a.ref_kind != "fact" or b.ref_kind not in ("fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    # Винительный = именительный для неодушевлённых kind-noun — отдельная
    # форма не нужна (как в v2).
    return f"{_quoted_cap(a, 'nom')} подтверждает {_quoted(b, 'nom')}."


def _verbalize_contradicts(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} противоречит {_quoted(b, 'nom')}."


def _verbalize_supersedes(a: Node, b: Node) -> str:
    if a.ref_kind not in ("decision", "fact") or b.ref_kind not in ("decision", "fact"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} заменяет собой {_quoted(b, 'nom')}."


def _verbalize_derived_from(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} {_BASED_ON_AGREE[a.ref_kind]} на {_quoted(b, 'prep')}."


def _verbalize_refers_to(a: Node, b: Node) -> str:
    if a.ref_kind not in ("fact", "decision") or b.ref_kind not in ("event", "fact", "decision"):
        return UNSUPPORTED_FOR_NLI
    return f"{_quoted_cap(a, 'nom')} ссылается на {_quoted(b, 'nom')}."


def _verbalize_related_to_entity(a: Node, b: Node) -> str:
    # Симметричная конструкция — переставленные аргументы дают ДРУГУЮ
    # строку, то же истинностное значение (как в v2).
    return f"Существует связь между «{_entity_label(a)}» и «{_entity_label(b)}»."


def _verbalize_related_to_atom(a: Node, b: Node) -> str:
    return f"Существует связь между {_quoted(a, 'ins')} и {_quoted(b, 'ins')}."


_VERBALIZERS = {
    ("involves", "ATOM", "ENTITY"): _verbalize_involves,
    ("has_role", "ENTITY", "ENTITY"): _verbalize_has_role,
    ("about", "ATOM", "ENTITY"): _verbalize_about,
    ("located_at", "ATOM", "ENTITY"): _verbalize_located_at,
    ("part_of", "ENTITY", "ENTITY"): _verbalize_part_of,
    ("created_by", "ATOM", "ENTITY"): _verbalize_created_by,
    ("owned_by", "ATOM", "ENTITY"): _verbalize_owned_by,
    ("resulted_in", "ATOM", "ATOM"): _verbalize_resulted_in,
    ("reason_for", "ATOM", "ATOM"): _verbalize_reason_for,
    ("supports", "ATOM", "ATOM"): _verbalize_supports,
    ("contradicts", "ATOM", "ATOM"): _verbalize_contradicts,
    ("supersedes", "ATOM", "ATOM"): _verbalize_supersedes,
    ("derived_from", "ATOM", "ATOM"): _verbalize_derived_from,
    ("refers_to", "ATOM", "ATOM"): _verbalize_refers_to,
    ("related_to", "ENTITY", "ENTITY"): _verbalize_related_to_entity,
    ("related_to", "ATOM", "ATOM"): _verbalize_related_to_atom,
}


def verbalize(relation_type: str, source: Node, target: Node) -> str:
    """Natural-language hypothesis или `UNSUPPORTED_FOR_NLI`, если для
    этой (relation_type, source.category, target.category) — или для
    конкретных `ref_kind` внутри неё — нет проверенного контракта
    (`docs/R4.6.F1.2-RELATION-ONTOLOGY.md`). Не форсирует строку ради
    покрытия enum."""
    fn = _VERBALIZERS.get((relation_type, source.category, target.category))
    if fn is None:
        return UNSUPPORTED_FOR_NLI
    return fn(source, target)

# ---- склеено из helm_core/knowledge/relation_benchmark_v3_fixtures.py ----
"""R4.6.F1.2 (владелец 03.09.2026) — ЗАМОРОЖЕННЫЙ benchmark v3 relation
NLI: новые, отдельные от `semantic_benchmark_fixtures.GOLDEN_CASES`
fixtures (владелец п.1: старые GOLDEN_CASES не трогать, историческая
сравнимость R4 не должна ломаться).

Контракт (владелец п.6-9):

- Каждый кейс явно объявляет ОБА списка — `entailed` (позитивы) и
  `not_entailed` (явные hard negatives с человекочитаемой `reason`).
  «Отсутствует в `entailed`» НИКОГДА не читается как «ложно» — это была
  методологическая дыра v1 (false_pair на эвристике «нет в gold»),
  здесь закрыта тем, что единственный источник негатива — explicit
  `not_entailed`, у каждого — причина, почему это неверно, написанная
  вручную ДО какого-либо прогона модели-кандидата.
- Одна пара узлов может одновременно нести НЕСКОЛЬКО типов связи
  (например, ATOM `about` понятия И `involves` человека) — это не
  конфликт, contract §14.9/`docs/R4.6.F1.2-RELATION-ONTOLOGY.md`.
- Все hypothesis строятся `RelationVerbalizerV3` (quoted reference) —
  ни одна negative-пара здесь не является структурно неверблизуемой
  (`UNSUPPORTED_FOR_NLI`) — иначе для неё нет NLI-примера, который можно
  было бы измерить.
- Покрытие (владелец п.7): ≥6 positive и ≥3 отдельных `case_id` на
  каждый из 15 `SemanticRelationType`; ≥2 явных hard negative на каждый
  positive (в среднем по кейсу, не жёстко 1:1 — см. coverage-тест).
  `HAS_ROLE` — обязательное отдельное покрытие (§14.9, R7): кейсы
  `clinic_visit_specialty`/`project_meeting_full` намеренно ставят
  `HAS_ROLE` (человек → понятие-специальность, атомонезависимо) РЯДОМ
  с `INVOLVES(role=...)` (человек — сторона ОДНОГО конкретного атома)
  на одном и том же человеке, чтобы прямо противопоставить эти два
  разных факта, а не смешать их.
- Fixtures — ручная работа автора этого файла (не тестируемой модели):
  ни `premise`, ни `entailed`/`not_entailed` не сгенерированы
  mDeBERTa/rubert и не сверялись с их выводом до заморозки.

Заморозка (владелец п.9): после коммита этого файла `split` каждого
`case_id` НЕ меняется. `final_holdout` — 4 кейса (17 positives),
покрывающие все 15 типов минимум по разу, использованные ТОЛЬКО для
финального отчёта, никогда для подбора порога. `calibration` — 16
кейсов (78 positives) — единственный источник LOOCV/threshold-подбора.
"""


from dataclasses import dataclass
from typing import Literal

Split = Literal["calibration", "final_holdout"]


@dataclass(frozen=True)
class RelEntity:
    ref: str
    entity_type: str
    label: str
    subtype: str | None = None


@dataclass(frozen=True)
class RelAtom:
    ref: str
    kind: str
    canonical_text: str


@dataclass(frozen=True)
class RelPositive:
    from_ref: str
    relation_type: str
    to_ref: str
    role: str | None = None


@dataclass(frozen=True)
class RelNegative:
    from_ref: str
    relation_type: str
    to_ref: str
    reason: str


@dataclass(frozen=True)
class RelationCaseV3:
    case_id: str
    split: Split
    domain: str
    text: str
    entities: tuple[RelEntity, ...] = ()
    atoms: tuple[RelAtom, ...] = ()
    entailed: tuple[RelPositive, ...] = ()
    not_entailed: tuple[RelNegative, ...] = ()
    notes: str = ""


RELATION_BENCHMARK_V3_CASES: tuple[RelationCaseV3, ...] = (
    RelationCaseV3(
        case_id="v3_clinic_visit_specialty",
        split="calibration",
        domain="health",
        text=(
            "15 марта 2026 года в клинике «Здоровье+» состоялся приём врача-нефролога "
            "Гавриловой Марины Сергеевны. Приём был посвящён теме хронической почечной "
            "недостаточности. Гаврилова Марина Сергеевна работает нефрологом уже двенадцать "
            "лет. В той же клинике по вторникам ведёт приём врач-кардиолог Орлов Дмитрий."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Гаврилова Марина Сергеевна"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="клиника «Здоровье+»"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="нефролог", subtype="medical_specialty"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="хроническая почечная недостаточность"),
            RelEntity(ref="e5", entity_type="PERSON", label="Орлов Дмитрий"),
            RelEntity(ref="e6", entity_type="CONCEPT", label="кардиолог", subtype="medical_specialty"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event",
                   canonical_text="15 марта 2026 года в клинике «Здоровье+» состоялся приём врача-нефролога Гавриловой Марины Сергеевны."),
            RelAtom(ref="a2", kind="fact", canonical_text="Приём был посвящён теме хронической почечной недостаточности."),
            RelAtom(ref="a3", kind="fact", canonical_text="В той же клинике по вторникам ведёт приём врач-кардиолог Орлов Дмитрий."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1", role="doctor"),
            RelPositive("a1", "located_at", "e2"),
            RelPositive("a2", "about", "e4"),
            RelPositive("e1", "has_role", "e3"),
            RelPositive("e5", "has_role", "e6"),
            RelPositive("a3", "located_at", "e2"),
        ),
        not_entailed=(
            RelNegative("a2", "about", "e3", "about относится к теме ХПН (e4), не к специальности нефролог (e3) — специальность фиксирует has_role, не about факта о содержании приёма."),
            RelNegative("e5", "has_role", "e4", "Орлов (e5) не связан текстом с темой ХПН (e4) как ролью — его специальность e6 (кардиолог)."),
            RelNegative("a1", "involves", "e5", "Орлов Дмитрий упомянут в отдельном предложении о другом дне приёма — не участник события a1."),
            RelNegative("a3", "involves", "e1", "a3 описывает приём Орлова по вторникам — Гаврилова в этом предложении не упомянута."),
            RelNegative("e1", "has_role", "e4", "e4 — название темы/болезни (ХПН), не профессиональная специальность Гавриловой; её специальность — e3 (нефролог)."),
            RelNegative("e1", "has_role", "e6", "Гаврилова — нефролог (e3), не кардиолог (e6) — специальность Орлова, не её."),
            RelNegative("e5", "has_role", "e3", "Орлов — кардиолог (e6), не нефролог (e3) — специальность Гавриловой, не его."),
            RelNegative("a3", "about", "e6", "a3 называет специальность Орлова через прямое упоминание в тексте («врач-кардиолог») — это структурная роль (has_role), не тематическая привязка факта (about)."),
            RelNegative("a2", "about", "e6", "a2 посвящена ХПН (e4), не кардиологии (e6) — тема Орлова текстом не связывается с этим фактом."),
            RelNegative("a2", "involves", "e5", "a2 — факт о теме приёма Гавриловой, Орлов в нём не упомянут."),
            RelNegative("a1", "about", "e4", "about в этом кейсе размечена для a2 (отдельного факта о теме приёма), не для a1 (события самого приёма)."),
            RelNegative("a1", "about", "e3", "about в этом кейсе размечена для темы ХПН (a2→e4); a1 описывает сам факт визита, а не 'тему' специальности нефролог."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_clinic_report_authorship",
        split="calibration",
        domain="health",
        text=(
            "Заключение по итогам обследования Ковалёва Артёма составлено на основе "
            "результатов анализов и подготовлено врачом-нефрологом Крыловой Анной Ивановной. "
            "Результаты анализов сделаны неделей ранее. Консультация от 2 марта 2026 года "
            "зафиксирована в отдельном протоколе. В тексте заключения есть ссылка на протокол "
            "консультации от 2 марта 2026 года. Заключение посвящено теме хронической болезни почек."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Крылова Анна Ивановна"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="хроническая болезнь почек"),
            RelEntity(ref="e3", entity_type="PERSON", label="Ковалёв Артём"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact",
                   canonical_text="Заключение по итогам обследования Ковалёва Артёма составлено на основе результатов анализов и подготовлено врачом-нефрологом Крыловой Анной Ивановной."),
            RelAtom(ref="a2", kind="fact", canonical_text="Результаты анализов сделаны неделей ранее."),
            RelAtom(ref="a3", kind="fact", canonical_text="Консультация от 2 марта 2026 года зафиксирована в отдельном протоколе."),
            RelAtom(ref="a4", kind="fact", canonical_text="В тексте заключения есть ссылка на протокол консультации от 2 марта 2026 года."),
            RelAtom(ref="a5", kind="fact", canonical_text="Заключение посвящено теме хронической болезни почек."),
        ),
        entailed=(
            RelPositive("a1", "created_by", "e1"),
            RelPositive("a1", "derived_from", "a2"),
            RelPositive("a4", "refers_to", "a3"),
            RelPositive("a5", "about", "e2"),
        ),
        not_entailed=(
            RelNegative("a2", "created_by", "e1", "Крылова готовила заключение (a1), а не сами результаты анализов (a2) — a2 создан лабораторией, текстом не названной."),
            RelNegative("a1", "created_by", "e3", "заключение подготовлено Крыловой (e1), не пациентом Ковалёвым (e3)."),
            RelNegative("a2", "derived_from", "a3", "результаты анализов (a2) не основаны на протоколе консультации (a3) — независимые источники, производности текст не утверждает."),
            RelNegative("a3", "refers_to", "a4", "reversed_direction: ссылку на протокол (a3) делает заключение (a4), не наоборот."),
            RelNegative("a1", "refers_to", "a3", "ссылку на протокол делает отдельное предложение a4, не сам факт об авторстве/основе a1."),
            RelNegative("a1", "about", "e2", "about в этом кейсе относится к a5 (отдельно сформулированной теме), не к a1 (факту об авторстве и основе)."),
            RelNegative("a5", "created_by", "e1", "a5 — про тему заключения, авторство утверждает a1, не a5 (разные факты)."),
            RelNegative("a4", "about", "e2", "a4 — факт о наличии ссылки на протокол, тему хронической болезни почек не упоминает; about размечена только для a5."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_clinic_diagnosis_conflict",
        split="calibration",
        domain="health",
        text=(
            "У пациента были жалобы, типичные для гастрита, и терапевт поставил диагноз "
            "«гастрит» на основании этих жалоб. По результатам гастроскопии врач-"
            "гастроэнтеролог поставил новый диагноз — язвенная болезнь, который заменил "
            "собой диагноз терапевта. Результаты гастроскопии показали наличие язвы и "
            "подтверждают новый диагноз. Предположение о гастрите противоречит результатам "
            "гастроскопии."
        ),
        atoms=(
            RelAtom(ref="a1", kind="decision", canonical_text="Терапевт поставил диагноз «гастрит» на основании жалоб пациента."),
            RelAtom(ref="a2", kind="fact", canonical_text="У пациента были жалобы, типичные для гастрита."),
            RelAtom(ref="a3", kind="decision", canonical_text="Врач-гастроэнтеролог поставил диагноз «язвенная болезнь» по результатам гастроскопии."),
            RelAtom(ref="a4", kind="fact", canonical_text="Результаты гастроскопии показали наличие язвы."),
        ),
        entailed=(
            RelPositive("a2", "reason_for", "a1"),
            RelPositive("a4", "supports", "a3"),
            RelPositive("a3", "supersedes", "a1"),
            RelPositive("a1", "contradicts", "a4"),
        ),
        not_entailed=(
            RelNegative("a4", "reason_for", "a1", "результаты гастроскопии (a4) не названы текстом причиной ПЕРВОГО диагноза (a1) — они появились позже и стали основанием для НОВОГО диагноза a3, а не a1."),
            RelNegative("a2", "supports", "a1", "a2 (жалобы) — основание диагноза (reason_for), а не независимое свидетельство, подтверждающее его (supports) — эти два факта в одном предложении играют разные роли."),
            RelNegative("a1", "supersedes", "a3", "reversed_direction: новый диагноз a3 заменяет старый a1, не наоборот."),
            RelNegative("a2", "contradicts", "a4", "жалобы пациента (a2) не противоречат результатам гастроскопии (a4) — последовательные звенья одного диагностического процесса, не взаимоисключающие утверждения."),
            RelNegative("a2", "supersedes", "a1", "a2 — жалобы (обоснование), не отдельное решение/диагноз, способное заменить a1."),
            RelNegative("a1", "contradicts", "a3", "a1 и a3 — сменяющие друг друга диагнозы (supersedes), а не одновременно заявленные несовместимые утверждения (contradicts) — текст явно называет a3 заменой a1."),
            RelNegative("a4", "reason_for", "a3", "a4 — свидетельство, подтверждающее диагноз (supports), не обоснование РЕШЕНИЯ поставить диагноз, отличное от самого диагноза."),
            RelNegative("a2", "contradicts", "a3", "жалобы пациента (a2) не противоречат новому диагнозу (a3) — они согласуются с более ранним диагнозом a1; явное противоречие текст фиксирует только между a1 и a4."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_project_meeting_full",
        split="calibration",
        domain="work",
        text=(
            "12 мая 2026 года в переговорной комнате офиса на Ленинском проспекте состоялось "
            "совещание отдела внедрения с участием руководителя проекта Волошина Артёма и "
            "аналитика Дементьевой Ольги. Волошин Артём отвечает за роль руководителя проекта "
            "уже второй год. Отдел внедрения входит в состав ООО «ТехноСтрой». Тестировщиков "
            "не хватало для соблюдения графика, и было решено перенести дату сдачи модуля на "
            "июнь. Перенос даты потребовал уведомить заказчика дополнительным письмом."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Волошин Артём"),
            RelEntity(ref="e2", entity_type="PERSON", label="Дементьева Ольга"),
            RelEntity(ref="e3", entity_type="PLACE", label="переговорная комната офиса на Ленинском проспекте"),
            RelEntity(ref="e4", entity_type="ORGANIZATION", label="Отдел внедрения"),
            RelEntity(ref="e5", entity_type="ORGANIZATION", label="ООО «ТехноСтрой»"),
            RelEntity(ref="e6", entity_type="CONCEPT", label="руководитель проекта", subtype="role_concept"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event",
                   canonical_text="12 мая 2026 года в переговорной комнате офиса на Ленинском проспекте состоялось совещание отдела внедрения с участием руководителя проекта Волошина Артёма и аналитика Дементьевой Ольги."),
            RelAtom(ref="a2", kind="fact", canonical_text="Волошин Артём отвечает за роль руководителя проекта уже второй год."),
            RelAtom(ref="a3", kind="fact", canonical_text="Тестировщиков не хватало для соблюдения графика."),
            RelAtom(ref="a4", kind="decision", canonical_text="Было решено перенести дату сдачи модуля на июнь."),
            RelAtom(ref="a5", kind="fact", canonical_text="Перенос даты потребовал уведомить заказчика дополнительным письмом."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1", role="project_manager"),
            RelPositive("a1", "involves", "e2", role="analyst"),
            RelPositive("a1", "located_at", "e3"),
            RelPositive("e1", "has_role", "e6"),
            RelPositive("e4", "part_of", "e5"),
            RelPositive("a3", "reason_for", "a4"),
            RelPositive("a4", "resulted_in", "a5"),
        ),
        not_entailed=(
            RelNegative("a1", "involves", "e5", "ООО «ТехноСтрой» упомянута только в связи с принадлежностью отдела (part_of); участие в самом совещании a1 текст не описывает."),
            RelNegative("e2", "has_role", "e6", "текст не называет Дементьеву руководителем проекта — эта роль зафиксирована только за Волошиным."),
            RelNegative("e5", "part_of", "e4", "reversed_direction: отдел — часть организации, не наоборот."),
            RelNegative("a5", "reason_for", "a4", "уведомление заказчика (a5) — следствие решения a4 (resulted_in), а не отдельная причина, обосновывающая это же решение задним числом."),
            RelNegative("a3", "resulted_in", "a5", "прямая цепь текста — a3 (причина) → a4 (решение) → a5 (следствие); a3 не назван текстом напрямую приведшим к a5, минуя a4."),
            RelNegative("a2", "involves", "e2", "a2 — факт о роли Волошина, Дементьева в нём не упомянута."),
            RelNegative("a1", "located_at", "e5", "a1 произошло в переговорной комнате (e3); ООО «ТехноСтрой» упомянута как владелец отдела, не как физическое место события."),
            RelNegative("a4", "involves", "e4", "a4 — решение о переносе даты; отдел явно не назван его 'участником' — involves говорит про сторону события/факта, не про то, кого решение касается."),
            RelNegative("a2", "reason_for", "a4", "a2 описывает многолетний факт о роли Волошина — причина решения о переносе даты — нехватка тестировщиков (a3), не a2."),
            RelNegative("a2", "located_at", "e3", "a2 — факт о роли Волошина, места не касается; located_at размечен только для a1 (событие совещания)."),
            RelNegative("a1", "about", "e6", "about не размечена для роли 'руководитель проекта' — эта роль зафиксирована как has_role (e1→e6), не тема события a1."),
            RelNegative("a3", "involves", "e2", "a3 — факт о нехватке тестировщиков, Дементьева в нём явно не упомянута."),
            RelNegative("a4", "created_by", "e1", "решение a4 сформулировано безлично ('было решено') — текст не называет Волошина его автором в явном виде."),
            RelNegative("a5", "involves", "e3", "a5 — факт об уведомлении заказчика, к переговорной комнате (e3) отношения не имеет."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_project_decision_chain",
        split="calibration",
        domain="work",
        text=(
            "Совет проекта «Горизонт-2» в марте принял решение использовать поставщика А. "
            "В апреле совет пересмотрел выбор и принял новое решение — перейти на поставщика "
            "Б, которое заменило собой мартовское решение. Переход на поставщика Б потребовал "
            "пересмотра бюджета проекта. Итоговый бюджетный отчёт составлен на основе "
            "апрельского решения. В отчёте есть ссылка на мартовское решение совета."
        ),
        atoms=(
            RelAtom(ref="a1", kind="decision", canonical_text="Совет проекта «Горизонт-2» в марте принял решение использовать поставщика А."),
            RelAtom(ref="a2", kind="decision", canonical_text="Совет пересмотрел выбор и принял новое решение — перейти на поставщика Б."),
            RelAtom(ref="a3", kind="fact", canonical_text="Переход на поставщика Б потребовал пересмотра бюджета проекта."),
            RelAtom(ref="a4", kind="fact", canonical_text="Итоговый бюджетный отчёт составлен на основе апрельского решения."),
            RelAtom(ref="a5", kind="fact", canonical_text="В отчёте есть ссылка на мартовское решение совета."),
        ),
        entailed=(
            RelPositive("a2", "supersedes", "a1"),
            RelPositive("a2", "resulted_in", "a3"),
            RelPositive("a4", "derived_from", "a2"),
            RelPositive("a5", "refers_to", "a1"),
        ),
        not_entailed=(
            RelNegative("a1", "supersedes", "a2", "reversed_direction: новое решение a2 заменяет старое a1, не наоборот."),
            RelNegative("a4", "resulted_in", "a3", "reversed_direction по времени: пересмотр бюджета (a3) предшествует отчёту (a4), отчёт не мог 'привести' к a3."),
            RelNegative("a2", "derived_from", "a4", "reversed_direction: отчёт a4 составлен на основе решения a2, не наоборот."),
            RelNegative("a1", "refers_to", "a5", "reversed_direction: ссылку на решение делает отчёт a5, не мартовское решение a1."),
            RelNegative("a1", "resulted_in", "a3", "текст явно связывает пересмотр бюджета (a3) с апрельским решением a2 («переход … потребовал»), не с мартовским a1."),
            RelNegative("a4", "derived_from", "a1", "отчёт составлен на основе АПРЕЛЬСКОГО решения (a2), не мартовского (a1) — текст это явно уточняет."),
            RelNegative("a5", "refers_to", "a2", "ссылка сделана на МАРТОВСКОЕ решение (a1), не на апрельское (a2)."),
            RelNegative("a3", "refers_to", "a1", "a3 — факт о пересмотре бюджета, ссылки на мартовское решение текст не делает — эту ссылку делает отдельно a5."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_org_structure",
        split="calibration",
        domain="work",
        text=(
            "Отдел маркетинга входит в состав ООО «Ромашка». ООО «Ромашка», в свою очередь, "
            "принадлежит холдингу АО «Агро-Инвест». Руководитель отдела маркетинга Белова "
            "Ирина занимает должность директора по маркетингу. Оборудование отдела — три "
            "ноутбука — принадлежит ООО «Ромашка»."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Отдел маркетинга"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Ромашка»"),
            RelEntity(ref="e3", entity_type="ORGANIZATION", label="АО «Агро-Инвест»"),
            RelEntity(ref="e4", entity_type="PERSON", label="Белова Ирина"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="директор по маркетингу", subtype="role_concept"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="ООО «Ромашка» принадлежит холдингу АО «Агро-Инвест»."),
            RelAtom(ref="a2", kind="fact", canonical_text="Оборудование отдела — три ноутбука — принадлежит ООО «Ромашка»."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "owned_by", "e3"),
            RelPositive("a2", "owned_by", "e2"),
            RelPositive("e4", "has_role", "e5"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("e2", "part_of", "e3", "текст описывает отношение владения холдингом (owned_by), не структурное включение ООО «Ромашка» в холдинг как организационной части (part_of)."),
            RelNegative("a1", "owned_by", "e2", "a1 — факт о владении холдингом АО «Агро-Инвест» (e3), не ООО «Ромашка» собой."),
            RelNegative("a2", "owned_by", "e3", "оборудование принадлежит ООО «Ромашка» (e2) по тексту напрямую, холдинг e3 в этом предложении не упомянут."),
            RelNegative("e3", "has_role", "e5", "холдинг АО «Агро-Инвест» (e3) не назван текстом обладателем роли директора по маркетингу — эта роль закреплена за Беловой Ириной (e4)."),
            RelNegative("e1", "has_role", "e5", "роль директора по маркетингу текст закрепляет за Беловой Ириной (e4) лично, не за отделом (e1) как организацией."),
            RelNegative("a2", "created_by", "e2", "a2 — факт о владении оборудованием, не о том, что ООО «Ромашка» его 'создала' — created_by здесь текстом не подтверждён."),
            RelNegative("a1", "owned_by", "e1", "a1 говорит о владении холдингом (e3) над ООО «Ромашка» (e2), не об отделе маркетинга (e1) — отдел здесь не упомянут."),
            RelNegative("a2", "refers_to", "a1", "a2 — факт о владении оборудованием, ссылки на факт о владении холдингом (a1) не делает — разные, отдельно утверждённые факты."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_purchase_ownership",
        split="calibration",
        domain="purchases",
        text=(
            "Принтер HP куплен 3 марта 2026 года в магазине «Комус» и принадлежит ООО "
            "«Ромашка». Гарантийный талон на принтер выпущен производителем HP. Чек об "
            "оплате подтверждает факт покупки принтера. Покупка была оформлена в офисе на "
            "Садовой улице."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="магазин «Комус»"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Ромашка»"),
            RelEntity(ref="e3", entity_type="ORGANIZATION", label="HP"),
            RelEntity(ref="e4", entity_type="PLACE", label="офис на Садовой улице"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Принтер HP куплен 3 марта 2026 года в магазине «Комус» и принадлежит ООО «Ромашка»."),
            RelAtom(ref="a2", kind="fact", canonical_text="Гарантийный талон на принтер выпущен производителем HP."),
            RelAtom(ref="a3", kind="fact", canonical_text="Чек об оплате подтверждает факт покупки принтера."),
            RelAtom(ref="a4", kind="fact", canonical_text="Покупка была оформлена в офисе на Садовой улице."),
        ),
        entailed=(
            RelPositive("a1", "located_at", "e1"),
            RelPositive("a1", "owned_by", "e2"),
            RelPositive("a2", "created_by", "e3"),
            RelPositive("a3", "supports", "a1"),
            RelPositive("a4", "located_at", "e4"),
        ),
        not_entailed=(
            RelNegative("a1", "owned_by", "e1", "принтер принадлежит ООО «Ромашка» (e2); магазин «Комус» — продавец (located_at), не владелец после покупки."),
            RelNegative("a1", "located_at", "e2", "a1 куплен в магазине «Комус» (e1); ООО «Ромашка» — владелец, не место покупки."),
            RelNegative("a2", "created_by", "e2", "гарантийный талон выпущен производителем HP (e3), не ООО «Ромашка»."),
            RelNegative("a1", "supports", "a3", "reversed_direction: чек (a3) подтверждает факт покупки (a1), не наоборот."),
            RelNegative("a4", "located_at", "e1", "оформление покупки произошло в офисе на Садовой улице (e4), не в магазине «Комус» (e1) — два разных места по тексту."),
            RelNegative("a3", "created_by", "e3", "чек не назван текстом созданным производителем HP — HP выпустил гарантийный талон (a2), не чек (a3)."),
            RelNegative("a2", "located_at", "e1", "a2 — факт о выпуске гарантийного талона производителем, места не касается — located_at размечен только для a1/a4."),
            RelNegative("a4", "owned_by", "e2", "a4 — факт об оформлении покупки в офисе, не о владении — владение зафиксировано в a1."),
            RelNegative("a3", "located_at", "e1", "a3 — факт о чеке, места не упоминает — located_at размечен только для a1 (покупка) и a4 (оформление)."),
            RelNegative("a1", "created_by", "e1", "a1 — факт о покупке и владении, магазин «Комус» — не создатель принтера (created_by относится к производителю HP, a2)."),
            RelNegative("a2", "refers_to", "a1", "a2 — факт о выпуске гарантийного талона, явной ссылки на факт покупки (a1) текст не делает."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_finance_concepts",
        split="calibration",
        domain="personal",
        text=(
            "Статья была посвящена теме дефляции. Дефляция — понятие, тесно связанное с "
            "инфляцией как противоположный процесс. Один аналитик утверждает, что дефляция "
            "полезна для потребителей, тогда как другой аналитик утверждает обратное — что "
            "дефляция вредна для экономики в целом, и это второе мнение прямо противоречит "
            "первому. В экономике действительно происходит дефляция — это подтверждает "
            "статистика по снижению цен за квартал."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="CONCEPT", label="дефляция"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="инфляция"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Статья была посвящена теме дефляции."),
            RelAtom(ref="a2", kind="fact", canonical_text="Один аналитик утверждает, что дефляция полезна для потребителей."),
            RelAtom(ref="a3", kind="fact", canonical_text="Другой аналитик утверждает, что дефляция вредна для экономики в целом."),
            RelAtom(ref="a4", kind="fact", canonical_text="Статистика по снижению цен за квартал зафиксирована."),
            RelAtom(ref="a5", kind="fact", canonical_text="В экономике действительно происходит дефляция."),
        ),
        entailed=(
            RelPositive("a1", "about", "e1"),
            RelPositive("e1", "related_to", "e2"),
            RelPositive("a3", "contradicts", "a2"),
            RelPositive("a4", "supports", "a5"),
        ),
        not_entailed=(
            RelNegative("a1", "about", "e2", "статья посвящена дефляции (e1), не инфляции (e2) — инфляция упомянута только для сравнения."),
            RelNegative("a2", "contradicts", "a5", "мнение о пользе дефляции (a2) не названо текстом противоречащим факту её наличия (a5) — противоречие текст фиксирует только между a2 и a3 (двумя мнениями аналитиков)."),
            RelNegative("a5", "supports", "a4", "reversed_direction: статистика (a4) подтверждает факт a5, не наоборот."),
            RelNegative("a4", "about", "e1", "about в этом кейсе размечена для a1 (статьи); a4 — статистика, отдельный факт, не переопределяет тему статьи."),
            RelNegative("a2", "supports", "a5", "мнение аналитика о пользе дефляции (a2) — оценочное суждение, не свидетельство её наличия; наличие дефляции подтверждает статистика (a4), не мнение a2."),
            RelNegative("a5", "about", "e2", "a5 утверждает наличие дефляции (e1), тема инфляции (e2) в нём не упомянута."),
            RelNegative("a3", "about", "e2", "a3 обсуждает вред дефляции, тему инфляции (e2) не затрагивает — about для e2 нигде текстом не подтверждается."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_lecture_series",
        split="calibration",
        domain="learning",
        text=(
            "На лекции по макроэкономике профессор Орлова Татьяна рассказала о понятии "
            "стагфляции. Стагфляция тесно связана с инфляцией, сочетая её с экономическим "
            "спадом. Орлова Татьяна — профессор кафедры экономики. Лекция также кратко "
            "затронула тему безработицы."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Орлова Татьяна"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="стагфляция"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="инфляция"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="профессор кафедры экономики", subtype="role_concept"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="безработица"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event", canonical_text="На лекции по макроэкономике профессор Орлова Татьяна рассказала о понятии стагфляции."),
            RelAtom(ref="a2", kind="fact", canonical_text="Лекция также кратко затронула тему безработицы."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1"),
            RelPositive("a1", "about", "e2"),
            RelPositive("e2", "related_to", "e3"),
            RelPositive("e1", "has_role", "e4"),
            RelPositive("a2", "about", "e5"),
        ),
        not_entailed=(
            RelNegative("a1", "about", "e3", "лекция явно рассказывает о стагфляции (e2); инфляция упомянута лишь как часть определения стагфляции, отдельной темой a1 текст её не называет."),
            RelNegative("e1", "has_role", "e3", "инфляция (e3) — понятие, упомянутое в связи со стагфляцией, не профессиональная роль Орловой — её роль зафиксирована как e4."),
            RelNegative("a2", "involves", "e1", "a2 — отдельное предложение про тему безработицы, Орлова в нём не названа участником явно (в отличие от a1)."),
            RelNegative("a1", "about", "e5", "тему безработицы текст относит к a2 («также кратко затронула» — отдельное, более позднее предложение), не к a1."),
            RelNegative("e3", "related_to", "e5", "текст не утверждает связь между инфляцией и безработицей — упомянуты в разных, не связанных по тексту предложениях."),
            RelNegative("a2", "about", "e2", "тема безработицы (e5) — отдельная от стагфляции (e2), про которую говорит a1; a2 её не упоминает."),
            RelNegative("e1", "has_role", "e5", "e5 — тема «безработица», не понятие-роль, связанное с профессией Орловой — её роль зафиксирована как e4."),
            RelNegative("e4", "related_to", "e2", "роль-понятие 'профессор кафедры экономики' (e4) текстом не связывается с темой инфляции (e2) — разные, не связанные явно понятия."),
            RelNegative("a2", "about", "e3", "a2 — факт о безработице, тему инфляции (e3) не упоминает."),
            RelNegative("e5", "related_to", "e2", "безработица (e5) и стагфляция (e2) не названы текстом связанными понятиями — только стагфляция явно связана с инфляцией (e3)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_legal_contract_dispute",
        split="calibration",
        domain="work",
        text=(
            "Первоначальный договор поставки предусматривал срок доставки 10 дней. Задержка "
            "на таможне возникла и стала причиной подписания дополнительного соглашения, "
            "которое заменило собой этот пункт договора, установив новый срок — 20 дней. "
            "Новый срок доставки привёл к пересмотру плана производства у покупателя. Один "
            "из менеджеров утверждает, что доставка укладывается в 10 дней, что прямо "
            "противоречит дополнительному соглашению. В переписке с покупателем есть ссылка "
            "на текст дополнительного соглашения."
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Первоначальный договор поставки предусматривал срок доставки 10 дней."),
            RelAtom(ref="a2", kind="decision", canonical_text="Стороны подписали дополнительное соглашение, установив новый срок — 20 дней."),
            RelAtom(ref="a3", kind="fact", canonical_text="Задержка на таможне возникла перед подписанием соглашения."),
            RelAtom(ref="a4", kind="fact", canonical_text="План производства у покупателя был пересмотрен."),
            RelAtom(ref="a5", kind="fact", canonical_text="Один из менеджеров утверждает, что доставка укладывается в 10 дней."),
            RelAtom(ref="a6", kind="fact", canonical_text="В переписке с покупателем есть ссылка на текст дополнительного соглашения."),
        ),
        entailed=(
            RelPositive("a2", "supersedes", "a1"),
            RelPositive("a3", "reason_for", "a2"),
            RelPositive("a2", "resulted_in", "a4"),
            RelPositive("a5", "contradicts", "a2"),
            RelPositive("a6", "refers_to", "a2"),
        ),
        not_entailed=(
            RelNegative("a1", "supersedes", "a2", "reversed_direction."),
            RelNegative("a1", "reason_for", "a2", "первоначальный договор (a1) — предмет замены, не причина подписания соглашения; причина — задержка на таможне (a3)."),
            RelNegative("a1", "resulted_in", "a4", "пересмотр плана производства (a4) вызван НОВЫМ сроком доставки, установленным соглашением a2, а не первоначальным договором a1."),
            RelNegative("a1", "contradicts", "a5", "менеджер утверждает про срок 10 дней, что совпадает с ПЕРВОНАЧАЛЬНЫМ договором (a1), а не противоречит ему — противоречие текст фиксирует именно с действующим доп.соглашением (a2)."),
            RelNegative("a2", "refers_to", "a6", "reversed_direction: ссылку на соглашение делает переписка (a6), не само соглашение (a2)."),
            RelNegative("a3", "supersedes", "a1", "a3 — факт о задержке (причина), не отдельное решение, способное заменить договор a1."),
            RelNegative("a3", "supersedes", "a2", "задержка (a3) — причина решения a2 (reason_for), не отдельное решение, заменяющее его (supersedes) — a3 не имеет статуса замены."),
            RelNegative("a6", "contradicts", "a1", "переписка (a6) лишь ссылается на текст соглашения (refers_to), не заявляет ничего, что противоречило бы первоначальному договору (a1)."),
            RelNegative("a5", "refers_to", "a1", "мнение менеджера (a5) не оформлено текстом как ссылка на договор — оно просто повторяет прежний срок, не цитируя документ."),
            RelNegative("a3", "refers_to", "a2", "задержка (a3) возникла ДО подписания соглашения (a2) — не может ссылаться на документ, которого ещё не существовало; вместо этого она — его причина (reason_for)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_publication_chain",
        split="calibration",
        domain="work",
        text=(
            "Годовой отчёт по продажам подготовлен аналитическим отделом. Аналитический "
            "отдел входит в состав ООО «Вектор». Отчёт составлен на основе данных из "
            "CRM-системы за год. В отчёте есть ссылка на презентацию по итогам третьего "
            "квартала."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Аналитический отдел"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Вектор»"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Годовой отчёт по продажам подготовлен аналитическим отделом."),
            RelAtom(ref="a2", kind="fact", canonical_text="Данные из CRM-системы за год были собраны и обработаны."),
            RelAtom(ref="a3", kind="fact", canonical_text="Презентация по итогам третьего квартала была представлена руководству."),
        ),
        entailed=(
            RelPositive("a1", "created_by", "e1"),
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "derived_from", "a2"),
            RelPositive("a1", "refers_to", "a3"),
        ),
        not_entailed=(
            RelNegative("a1", "created_by", "e2", "отчёт подготовлен именно аналитическим отделом (e1) как непосредственным автором; ООО «Вектор» — организация верхнего уровня, текст не называет её автором отчёта."),
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a2", "derived_from", "a1", "reversed_direction: отчёт (a1) основан на данных CRM (a2), не наоборот."),
            RelNegative("a3", "refers_to", "a1", "reversed_direction: ссылку на презентацию делает отчёт (a1), не наоборот."),
            RelNegative("a2", "created_by", "e1", "a2 — факт о сборе данных CRM; CRM-данные не авторский документ, а исходный материал (об этом говорит derived_from, не created_by)."),
            RelNegative("a2", "refers_to", "a3", "a2 — факт о сборе данных CRM, ссылки на презентацию (a3) не делает — эту ссылку делает сам отчёт (a1)."),
            RelNegative("a3", "derived_from", "a2", "презентация (a3) упомянута только как ссылочный документ (refers_to из a1), текст не утверждает, что она построена на данных CRM (a2)."),
            RelNegative("a1", "owned_by", "e2", "a1 говорит об авторстве отчёта (created_by), не о владении им (owned_by) — эти два разных факта текст не смешивает."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_family_property",
        split="calibration",
        domain="personal",
        text=(
            "Дача в посёлке Сосновка принадлежит Кузнецову Петру. Урожай яблок в этом году "
            "был собран на участке дачи. Кузнецов Пётр увлекается ландшафтным дизайном на "
            "территории дачи. Тема садоводства тесно связана с темой ландшафтного дизайна."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Кузнецов Пётр"),
            RelEntity(ref="e2", entity_type="PLACE", label="дача в посёлке Сосновка"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="садоводство"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="ландшафтный дизайн"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Дача в посёлке Сосновка принадлежит Кузнецову Петру."),
            RelAtom(ref="a2", kind="fact", canonical_text="Урожай яблок в этом году был собран на участке дачи."),
            RelAtom(ref="a3", kind="fact", canonical_text="Кузнецов Пётр увлекается ландшафтным дизайном на территории дачи."),
        ),
        entailed=(
            RelPositive("a1", "owned_by", "e1"),
            RelPositive("a2", "located_at", "e2"),
            RelPositive("a3", "located_at", "e2"),
            RelPositive("a3", "involves", "e1"),
            RelPositive("a3", "about", "e4"),
            RelPositive("e3", "related_to", "e4"),
        ),
        not_entailed=(
            RelNegative("a2", "owned_by", "e1", "a2 описывает сбор урожая, не факт владения дачей — владение утверждает только a1."),
            RelNegative("a3", "owned_by", "e1", "a3 описывает деятельность (хобби), а не факт владения — владение утверждает только a1."),
            RelNegative("a3", "about", "e3", "a3 явно называет темой ландшафтный дизайн (e4); садоводство (e3) — отдельное, хоть и связанное понятие, но не тема именно этого предложения."),
            RelNegative("a2", "involves", "e1", "a2 сообщает про урожай, не упоминает Кузнецова Петра явно как участника события."),
            RelNegative("a1", "involves", "e1", "a1 — статичный факт владения, не описание события с активным участием (в отличие от a3)."),
            RelNegative("a2", "about", "e3", "a2 — факт о сборе урожая; текст явно не формулирует его как 'про тему садоводства' (about размечена только для a3→e4)."),
            RelNegative("a1", "about", "e3", "a1 — факт о владении дачей, темы садоводства (e3) не касается — about размечена только для a3→e4."),
            RelNegative("a1", "about", "e4", "a1 не упоминает ландшафтный дизайн (e4) — тема закреплена за a3."),
            RelNegative("a2", "about", "e4", "a2 — факт о сборе урожая, темы ландшафтного дизайна (e4) не касается — эта тема закреплена за a3."),
            RelNegative("e3", "related_to", "e1", "садоводство (e3) как понятие текстом не связывается с личностью Кузнецова (e1) отношением related_to — он лишь увлекается им (involves/about), это не понятийная связь двух тем."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_clinic_visit_specialty_2",
        split="final_holdout",
        domain="health",
        text=(
            "22 июня 2026 года в кабинете эндокринолога поликлиники №4 состоялся приём "
            "пациента Фомина Сергея у врача Лебедевой Ольги Николаевны. Приём касался темы "
            "сахарного диабета второго типа. Лебедева Ольга Николаевна — эндокринолог с "
            "пятнадцатилетним стажем."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Лебедева Ольга Николаевна"),
            RelEntity(ref="e2", entity_type="PERSON", label="Фомин Сергей"),
            RelEntity(ref="e3", entity_type="ORGANIZATION", label="поликлиника №4"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="сахарный диабет второго типа"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="эндокринолог", subtype="medical_specialty"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event",
                   canonical_text="22 июня 2026 года в кабинете эндокринолога поликлиники №4 состоялся приём пациента Фомина Сергея у врача Лебедевой Ольги Николаевны."),
            RelAtom(ref="a2", kind="fact", canonical_text="Приём касался темы сахарного диабета второго типа."),
        ),
        entailed=(
            RelPositive("a1", "involves", "e1", role="doctor"),
            RelPositive("a1", "involves", "e2", role="patient"),
            RelPositive("a1", "located_at", "e3"),
            RelPositive("a2", "about", "e4"),
            RelPositive("e1", "has_role", "e5"),
        ),
        not_entailed=(
            RelNegative("e2", "has_role", "e4", "диагноз (e4) не является профессиональной ролью пациента Фомина (e2) — роль в этом кейсе зафиксирована только за Лебедевой (e1) её специальностью (e5)."),
            RelNegative("a2", "about", "e5", "приём касался темы диабета (e4), не специальности врача (e5) — специальность фиксирует has_role, не about этого факта."),
            RelNegative("e2", "has_role", "e5", "текст называет эндокринологом Лебедеву (e1), не пациента Фомина (e2)."),
            RelNegative("a2", "involves", "e2", "a2 — отдельный факт о теме приёма; участник (Фомин) назван в a1, в a2 явно не переутверждается."),
            RelNegative("a1", "about", "e4", "about относится к a2 (факту о теме), не к a1 (событию приёма)."),
            RelNegative("e1", "has_role", "e4", "e4 — диагноз/тема (сахарный диабет), не профессиональная специальность Лебедевой — её специальность e5."),
            RelNegative("a1", "about", "e5", "специальность эндокринолог (e5) — не тема события a1, а профессиональная роль Лебедевой (has_role)."),
            RelNegative("a2", "located_at", "e3", "a2 — факт о теме приёма, место (поликлиника №4) относится к событию a1, не переутверждается в a2."),
            RelNegative("a2", "involves", "e1", "a2 — факт о теме приёма, участников явно не называет — участие Лебедевой зафиксировано в a1."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_project_meeting_2",
        split="final_holdout",
        domain="work",
        text=(
            "Отдел логистики входит в состав ООО «Карго Плюс». 8 июля 2026 года руководитель "
            "отдела логистики Титов Игорь провёл совещание с водителями по поводу задержек "
            "поставок. Водителей не хватало, и это стало причиной решения нанять двух новых "
            "сотрудников. Найм новых сотрудников привёл к сокращению среднего времени "
            "доставки."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Отдел логистики"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Карго Плюс»"),
            RelEntity(ref="e3", entity_type="PERSON", label="Титов Игорь"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="event", canonical_text="8 июля 2026 года руководитель отдела логистики Титов Игорь провёл совещание с водителями по поводу задержек поставок."),
            RelAtom(ref="a2", kind="fact", canonical_text="Водителей не хватало."),
            RelAtom(ref="a3", kind="decision", canonical_text="Было решено нанять двух новых сотрудников."),
            RelAtom(ref="a4", kind="fact", canonical_text="Среднее время доставки сократилось."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "involves", "e3"),
            RelPositive("a2", "reason_for", "a3"),
            RelPositive("a3", "resulted_in", "a4"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a4", "reason_for", "a3", "a4 — следствие решения a3 (resulted_in), не его причина; причина решения — нехватка водителей (a2)."),
            RelNegative("a1", "resulted_in", "a4", "совещание (a1) не названо текстом напрямую приведшим к сокращению времени доставки (a4) — между ними стоит решение a3, которое и есть непосредственная причина a4."),
            RelNegative("a1", "involves", "e1", "a1 упоминает отдел логистики только как организационную принадлежность Титова, не как отдельного участника совещания наравне с людьми."),
            RelNegative("a2", "resulted_in", "a4", "нехватка водителей (a2) не названа текстом напрямую приведшей к сокращению времени доставки (a4) — между ними стоит решение a3."),
            RelNegative("a1", "resulted_in", "a2", "совещание (a1) не названо текстом причиной нехватки водителей (a2) — наоборот, нехватка предшествует и объясняет созыв совещания."),
            RelNegative("a2", "involves", "e3", "a2 — факт о нехватке водителей, Титов в нём явно не назван — его участие зафиксировано в a1."),
            RelNegative("a3", "involves", "e3", "решение a3 сформулировано безлично ('было решено') — текст не называет Титова его автором/участником явно."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_decision_supersede_2",
        split="final_holdout",
        domain="work",
        text=(
            "Первая версия регламента устанавливала лимит расходов в 50000 рублей. Вторая "
            "версия регламента заменила собой первую, установив лимит в 80000 рублей, и "
            "составлена на основе анализа фактических расходов за полугодие. Финансовый "
            "директор утверждает, что лимит остаётся прежним — 50000 рублей, что прямо "
            "противоречит второй версии регламента. Отчёт по фактическим расходам "
            "подтверждает обоснованность нового лимита."
        ),
        atoms=(
            RelAtom(ref="a1", kind="decision", canonical_text="Первая версия регламента устанавливала лимит расходов в 50000 рублей."),
            RelAtom(ref="a2", kind="decision", canonical_text="Вторая версия регламента установила лимит расходов в 80000 рублей."),
            RelAtom(ref="a3", kind="fact", canonical_text="Проведён анализ фактических расходов за полугодие."),
            RelAtom(ref="a4", kind="fact", canonical_text="Финансовый директор утверждает, что лимит остаётся прежним — 50000 рублей."),
            RelAtom(ref="a5", kind="fact", canonical_text="Отчёт по фактическим расходам подтверждает обоснованность нового лимита."),
        ),
        entailed=(
            RelPositive("a2", "supersedes", "a1"),
            RelPositive("a2", "derived_from", "a3"),
            RelPositive("a4", "contradicts", "a2"),
            RelPositive("a5", "supports", "a2"),
        ),
        not_entailed=(
            RelNegative("a1", "supersedes", "a2", "reversed_direction."),
            RelNegative("a3", "derived_from", "a2", "reversed_direction."),
            RelNegative("a4", "supports", "a2", "мнение финансового директора (a4) прямо ПРОТИВОРЕЧИТ второй версии (a2) — это отношение зафиксировано как contradicts, не supports."),
            RelNegative("a1", "contradicts", "a4", "a4 повторяет значение первой версии (a1) — они совпадают, не противоречат друг другу; противоречие текст фиксирует между a4 и действующей второй версией (a2)."),
            RelNegative("a4", "supersedes", "a2", "мнение финансового директора (a4) — не формально принятая версия регламента, способная заменить a2; текст не называет её решением с таким статусом."),
            RelNegative("a1", "derived_from", "a3", "анализ расходов (a3) стал основой именно ВТОРОЙ версии (a2) по тексту, не первой (a1)."),
            RelNegative("a3", "supports", "a1", "анализ расходов (a3) — основа ВТОРОЙ версии (a2, derived_from), не свидетельство в пользу ПЕРВОЙ версии (a1)."),
            RelNegative("a5", "contradicts", "a1", "текст явно фиксирует противоречие только между a4 и a2; a5 отдельно этого утверждения не делает — оно лишь подтверждает a2 (supports)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_purchase_2",
        split="final_holdout",
        domain="purchases",
        text=(
            "Автомобиль Toyota Camry принадлежит Никитиной Елене. Инструкция по эксплуатации "
            "автомобиля выпущена производителем Toyota. Договор купли-продажи содержит "
            "ссылку на паспорт транспортного средства, оформленный при регистрации "
            "автомобиля. Тема технического обслуживания автомобиля тесно связана с темой "
            "безопасности дорожного движения."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="PERSON", label="Никитина Елена"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="Toyota"),
            RelEntity(ref="e3", entity_type="CONCEPT", label="техническое обслуживание автомобиля"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="безопасность дорожного движения"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Автомобиль Toyota Camry принадлежит Никитиной Елене."),
            RelAtom(ref="a2", kind="fact", canonical_text="Инструкция по эксплуатации автомобиля выпущена производителем Toyota."),
            RelAtom(ref="a3", kind="fact", canonical_text="Договор купли-продажи содержит ссылку на паспорт транспортного средства."),
            RelAtom(ref="a4", kind="fact", canonical_text="Паспорт транспортного средства оформлен при регистрации автомобиля."),
        ),
        entailed=(
            RelPositive("a1", "owned_by", "e1"),
            RelPositive("a2", "created_by", "e2"),
            RelPositive("a3", "refers_to", "a4"),
            RelPositive("e3", "related_to", "e4"),
        ),
        not_entailed=(
            RelNegative("a2", "owned_by", "e2", "инструкция выпущена производителем (created_by) — это не факт владения автомобилем; владелец — Никитина Елена (a1)."),
            RelNegative("a1", "created_by", "e1", "a1 — факт о владении (owned_by), не об авторстве/создании — Никитина владеет автомобилем, не 'создала' его."),
            RelNegative("a4", "refers_to", "a3", "reversed_direction: ссылку на паспорт делает договор (a3), не наоборот."),
            RelNegative("a3", "refers_to", "a2", "договор ссылается на паспорт ТС (a4) по тексту, не на инструкцию по эксплуатации (a2) — разные документы."),
            RelNegative("a2", "created_by", "e1", "инструкцию выпустил производитель Toyota (e2), не владелица автомобиля Никитина (e1)."),
            RelNegative("a1", "refers_to", "a4", "a1 — факт о владении автомобилем, ссылки на паспорт ТС не делает — эту ссылку делает договор купли-продажи (a3)."),
            RelNegative("a2", "refers_to", "a4", "a2 — факт о выпуске инструкции производителем, паспорт ТС (a4) в нём не упоминается."),
            RelNegative("e3", "related_to", "e1", "тема техобслуживания (e3) не связана текстом с личностью Никитиной (e1) — она лишь владелица автомобиля, не связанное понятие."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_supply_chain_review",
        split="calibration",
        domain="work",
        text=(
            "Отдел закупок входит в состав ООО «Вектор Снаб». Отчёт по закупкам за квартал "
            "подготовлен отделом закупок; в отчёте есть ссылка на договор с поставщиком "
            "металлопроката. Складское оборудование принадлежит ООО «Вектор Снаб»."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Отдел закупок"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="ООО «Вектор Снаб»"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Отчёт по закупкам за квартал подготовлен отделом закупок; в отчёте есть ссылка на договор с поставщиком металлопроката."),
            RelAtom(ref="a2", kind="fact", canonical_text="Складское оборудование принадлежит ООО «Вектор Снаб»."),
            RelAtom(ref="a3", kind="fact", canonical_text="Договор с поставщиком металлопроката зафиксировал условия поставки."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "created_by", "e1"),
            RelPositive("a2", "owned_by", "e2"),
            RelPositive("a1", "refers_to", "a3"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a1", "created_by", "e2", "отчёт подготовлен отделом закупок (e1) как непосредственным автором, не организацией верхнего уровня ООО «Вектор Снаб» (e2)."),
            RelNegative("a2", "owned_by", "e1", "складское оборудование принадлежит ООО «Вектор Снаб» (e2) по тексту, не отделу закупок (e1) отдельно."),
            RelNegative("a3", "refers_to", "a1", "reversed_direction: ссылку на договор делает отчёт (a1), не наоборот."),
            RelNegative("a2", "created_by", "e1", "a2 — факт о владении оборудованием, не об авторстве/создании."),
            RelNegative("a1", "owned_by", "e2", "a1 — факт об авторстве отчёта (created_by), не о владении."),
            RelNegative("a2", "refers_to", "a3", "a2 — факт о владении складским оборудованием, ссылки на договор с поставщиком (a3) не делает — эту ссылку делает отчёт (a1)."),
            RelNegative("a3", "created_by", "e1", "a3 — факт о договоре с поставщиком, текст не называет отдел закупок (e1) его автором — авторство относится к отчёту (a1), не к самому договору."),
            RelNegative("a1", "located_at", "e2", "a1 — факт об авторстве и содержании отчёта, места текст не упоминает — located_at здесь не установлен."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_research_conclusions",
        split="calibration",
        domain="work",
        text=(
            "Пилотное тестирование показало высокий процент брака, и это стало причиной "
            "решения остановить производственную линию. Из-за остановки линии производство "
            "простаивало два дня. Повторные замеры подтверждают, что процент брака "
            "действительно был высоким. Первоначальный отчёт по качеству утверждал, что брак "
            "находится в пределах нормы, что прямо противоречит результатам пилотного "
            "тестирования. Итоговый отчёт по качеству зафиксировал реальный высокий процент "
            "брака и заменил собой первоначальный отчёт."
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Пилотное тестирование показало высокий процент брака."),
            RelAtom(ref="a2", kind="decision", canonical_text="Было решено остановить производственную линию."),
            RelAtom(ref="a3", kind="fact", canonical_text="Производство простаивало два дня."),
            RelAtom(ref="a4", kind="fact", canonical_text="Повторные замеры подтверждают, что процент брака действительно был высоким."),
            RelAtom(ref="a5", kind="fact", canonical_text="Первоначальный отчёт по качеству утверждал, что брак находится в пределах нормы."),
            RelAtom(ref="a6", kind="fact", canonical_text="Итоговый отчёт по качеству зафиксировал реальный высокий процент брака."),
        ),
        entailed=(
            RelPositive("a1", "reason_for", "a2"),
            RelPositive("a2", "resulted_in", "a3"),
            RelPositive("a4", "supports", "a1"),
            RelPositive("a5", "contradicts", "a1"),
            RelPositive("a6", "supersedes", "a5"),
        ),
        not_entailed=(
            RelNegative("a4", "reason_for", "a2", "причина решения остановить линию — исходный результат пилотного тестирования (a1), не повторные замеры (a4), которые лишь подтверждают его позже."),
            RelNegative("a4", "resulted_in", "a3", "повторные замеры (a4) не названы текстом причиной простоя (a3) — простой вызван решением остановить линию (a2), а не замерами."),
            RelNegative("a1", "supports", "a4", "reversed_direction: повторные замеры (a4) подтверждают пилотный результат (a1), не наоборот."),
            RelNegative("a4", "contradicts", "a5", "a4 (повторные замеры) не названы текстом стороной прямого противоречия — оно зафиксировано между a5 и a1 (пилотным тестированием); a4 лишь независимо подтверждает a1."),
            RelNegative("a5", "supersedes", "a6", "reversed_direction: итоговый отчёт (a6) заменяет первоначальный (a5), не наоборот."),
            RelNegative("a1", "supersedes", "a5", "a1 — факт о результатах пилотного тестирования, не отдельный 'отчёт по качеству', способный формально заменить a5 — эту роль текст отводит a6."),
            RelNegative("a6", "contradicts", "a1", "итоговый отчёт (a6) подтверждает результат пилотного тестирования (a1) тем же выводом — противоречие текст фиксирует только между a5 и a1, не a6 и a1."),
            RelNegative("a5", "supersedes", "a1", "a5 — противоречащее утверждение (contradicts), не формальная замена результатов пилотного тестирования (supersedes применим только к a6→a5)."),
            RelNegative("a3", "reason_for", "a2", "простой (a3) — следствие решения a2 (resulted_in), возник ПОСЛЕ него — не может быть его причиной."),
            RelNegative("a6", "derived_from", "a5", "a6 заменяет a5 (supersedes) по тексту явно; о том, что a6 составлен НА ОСНОВЕ a5 (derived_from), текст не говорит."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_editorial_workflow",
        split="calibration",
        domain="work",
        text=(
            "Редакция новостного отдела входит в состав издательского дома «Медиа Групп». "
            "Итоговая статья написана редактором Соколовой Верой на основе черновика, "
            "подготовленного стажёром. Черновик, в свою очередь, основан на исходном "
            "пресс-релизе компании-заказчика. Тема статьи — цифровая трансформация — тесно "
            "связана с темой автоматизации бизнес-процессов."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="ORGANIZATION", label="Редакция новостного отдела"),
            RelEntity(ref="e2", entity_type="ORGANIZATION", label="издательский дом «Медиа Групп»"),
            RelEntity(ref="e3", entity_type="PERSON", label="Соколова Вера"),
            RelEntity(ref="e4", entity_type="CONCEPT", label="цифровая трансформация"),
            RelEntity(ref="e5", entity_type="CONCEPT", label="автоматизация бизнес-процессов"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Итоговая статья написана редактором Соколовой Верой."),
            RelAtom(ref="a2", kind="fact", canonical_text="Стажёр подготовил черновик статьи."),
            RelAtom(ref="a3", kind="fact", canonical_text="Исходный пресс-релиз компании-заказчика содержал основные факты."),
        ),
        entailed=(
            RelPositive("e1", "part_of", "e2"),
            RelPositive("a1", "created_by", "e3"),
            RelPositive("a1", "derived_from", "a2"),
            RelPositive("a2", "derived_from", "a3"),
            RelPositive("e4", "related_to", "e5"),
        ),
        not_entailed=(
            RelNegative("e2", "part_of", "e1", "reversed_direction."),
            RelNegative("a1", "created_by", "e1", "статья написана конкретным редактором Соколовой Верой (e3), не отделом (e1) как таковым — текст называет автором именно человека."),
            RelNegative("a2", "derived_from", "a1", "reversed_direction: итоговая статья (a1) основана на черновике (a2), не наоборот."),
            RelNegative("a3", "derived_from", "a2", "reversed_direction: черновик (a2) основан на пресс-релизе (a3), не наоборот."),
            RelNegative("a1", "derived_from", "a3", "текст описывает цепочку a3→a2→a1 (пресс-релиз → черновик → статья), а не прямую связь a1 напрямую с a3, минуя черновик."),
            RelNegative("e4", "related_to", "e3", "тема цифровой трансформации не связана текстом с личностью Соколовой Веры — она лишь автор статьи об этой теме, не связанное понятие."),
            RelNegative("a3", "created_by", "e3", "пресс-релиз (a3) — исходный материал компании-заказчика; Соколова (e3) — автор итоговой статьи (a1), не пресс-релиза."),
            RelNegative("a2", "created_by", "e3", "черновик (a2) подготовлен стажёром, не редактором Соколовой — её авторство относится к итоговой статье (a1)."),
            RelNegative("a1", "refers_to", "a3", "текст описывает производную цепочку (derived_from: a3→a2→a1), не факт явной ссылки — refers_to для этой пары текстом не установлен."),
            RelNegative("e5", "related_to", "e3", "тема автоматизации бизнес-процессов (e5) не связана текстом с личностью Соколовой Веры (e3) — она автор статьи, не понятие."),
            RelNegative("a3", "refers_to", "a2", "пресс-релиз (a3) существовал до черновика (a2) — не может ссылаться на документ, которого тогда не было; напротив, черновик основан на пресс-релизе (derived_from)."),
        ),
    ),
    RelationCaseV3(
        case_id="v3_weather_delay_chain",
        split="calibration",
        domain="personal",
        text=(
            "Начался сильный снегопад, и это стало причиной решения перенести рейс на "
            "следующий день. Из-за переноса рейса бронь отеля пришлось продлить. Данные "
            "метеослужбы подтверждают, что снегопад был аномально сильным. Один из "
            "пассажиров утверждает, что снегопад был обычным для этого сезона, что прямо "
            "противоречит данным метеослужбы. Позже авиакомпания выпустила новое расписание, "
            "которое заменило собой перенесённый рейс ещё одной датой."
        ),
        entities=(
            RelEntity(ref="e1", entity_type="CONCEPT", label="аномальные снегопады"),
            RelEntity(ref="e2", entity_type="CONCEPT", label="изменение климата"),
        ),
        atoms=(
            RelAtom(ref="a1", kind="fact", canonical_text="Начался сильный снегопад."),
            RelAtom(ref="a2", kind="decision", canonical_text="Было решено перенести рейс на следующий день."),
            RelAtom(ref="a3", kind="fact", canonical_text="Бронь отеля пришлось продлить."),
            RelAtom(ref="a4", kind="fact", canonical_text="Данные метеослужбы подтверждают, что снегопад был аномально сильным."),
            RelAtom(ref="a5", kind="fact", canonical_text="Один из пассажиров утверждает, что снегопад был обычным для этого сезона."),
            RelAtom(ref="a6", kind="decision", canonical_text="Авиакомпания выпустила новое расписание с ещё одной датой рейса."),
        ),
        entailed=(
            RelPositive("a1", "reason_for", "a2"),
            RelPositive("a2", "resulted_in", "a3"),
            RelPositive("a4", "supports", "a1"),
            RelPositive("a5", "contradicts", "a4"),
            RelPositive("a6", "supersedes", "a2"),
            RelPositive("e1", "related_to", "e2"),
        ),
        not_entailed=(
            RelNegative("a4", "reason_for", "a2", "причина решения о переносе рейса — сам факт начавшегося снегопада (a1), не данные метеослужбы (a4), которые лишь подтверждают его силу отдельно."),
            RelNegative("a4", "resulted_in", "a3", "данные метеослужбы (a4) не названы текстом причиной продления брони (a3) — продление вызвано решением о переносе рейса (a2)."),
            RelNegative("a1", "supports", "a4", "reversed_direction: данные метеослужбы (a4) подтверждают факт снегопада (a1), не наоборот."),
            RelNegative("a1", "contradicts", "a5", "мнение пассажира (a5) противоречит именно ДАННЫМ метеослужбы об аномальности (a4), а не самому факту снегопада (a1) — a1 лишь констатирует, что снегопад был, без оценки его силы."),
            RelNegative("a2", "supersedes", "a6", "reversed_direction: новое расписание (a6) заменяет прежнее решение о переносе (a2), не наоборот."),
            RelNegative("a3", "related_to", "a4", "продление брони отеля (a3) и данные метеослужбы (a4) не названы текстом связанными напрямую сверх причинно-следственной цепочки через a1/a2."),
            RelNegative("a3", "contradicts", "a4", "продление брони (a3) не противоречит данным метеослужбы (a4) — разные, не конфликтующие факты."),
            RelNegative("a6", "contradicts", "a1", "новое расписание (a6) не противоречит факту снегопада (a1) — оно лишь заменяет более раннее решение a2 (supersedes)."),
            RelNegative("a5", "supersedes", "a2", "мнение пассажира (a5) — не формальное решение, способное заменить a2; отношение a5 к тексту — contradicts (с a4), не supersedes."),
            RelNegative("a4", "reason_for", "a6", "текст не называет данные метеослужбы (a4) причиной выпуска нового расписания (a6) — a6 представлен просто как более позднее решение, заменяющее a2."),
            RelNegative("a3", "refers_to", "a1", "продление брони (a3) — следствие решения a2 (resulted_in), не факт явной ссылки на снегопад (a1) — refers_to здесь не установлен текстом."),
            RelNegative("a3", "related_to", "a5", "продление брони (a3) и мнение пассажира о силе снегопада (a5) не названы текстом связанными — разные, не пересекающиеся утверждения."),
        ),
    ),
)


# ---- склеено из helm_core/knowledge/nli_relation_dataset_v3.py (относительные импорты переписаны на прямые ссылки, пакета helm_core здесь нет) ----
"""R4.6.F1.2 (владелец 03.09.2026) — детерминированный NLI dataset из
ЗАМОРОЖЕННОГО `relation_benchmark_v3_fixtures.RELATION_BENCHMARK_V3_CASES`,
через `RelationVerbalizerV3` (quoted reference, не родовая ссылка v2 и не
`canonical_text`-как-именная-группа v1). В отличие от v1/v2 (`build_examples`
в `nli_relation_dataset.py`) hard negatives здесь НЕ выводятся эвристикой
(`wrong_type`/`reversed_direction` циклическим сдвигом, `false_pair` на
«нет в gold = ложно») — они explicit, объявлены вручную в самих fixtures
(`RelationCaseV3.not_entailed`, каждый с `reason`), это и есть точка
R4.6.F1.1/F1.2: false_pair v1 был методологически недоказан.

`split` каждого примера наследуется от `RelationCaseV3.split` — заморожен
на уровне fixtures, здесь только прокидывается, не решается заново."""


from dataclasses import dataclass
from typing import Literal

import types as _types
v3 = _types.SimpleNamespace(Node=Node, verbalize=verbalize, UNSUPPORTED_FOR_NLI=UNSUPPORTED_FOR_NLI)

Split = Literal["calibration", "final_holdout"]


@dataclass(frozen=True)
class NliExampleV3:
    case_id: str
    split: Split
    premise: str
    hypothesis: str
    #: Ожидаемая метка entailment — True только для примеров, построенных
    #: из `RelationCaseV3.entailed`.
    entailed: bool
    relation_type: str
    from_ref: str
    to_ref: str


def _node(case: RelationCaseV3, ref: str) -> v3.Node:
    for e in case.entities:
        if e.ref == ref:
            return v3.Node(category="ENTITY", ref_kind=e.entity_type, label=e.label)
    for a in case.atoms:
        if a.ref == ref:
            return v3.Node(category="ATOM", ref_kind=a.kind, label=a.canonical_text)
    raise KeyError(f"{case.case_id}: ref {ref!r} не найден ни среди entities, ни среди atoms")


def build_examples_v3(cases: tuple[RelationCaseV3, ...] = RELATION_BENCHMARK_V3_CASES) -> list[NliExampleV3]:
    """Детерминированный список: один пример на каждый `entailed` +
    каждый `not_entailed` во всех кейсах, в порядке их объявления.
    Падает (`AssertionError`), если freeze-контракт нарушен и
    какая-то пара оказалась `UNSUPPORTED_FOR_NLI` — это должно быть
    исключено `test_knowledge_relation_benchmark_v3_fixtures.py` ДО
    вызова этой функции, здесь — defense in depth, не первичная проверка."""
    examples: list[NliExampleV3] = []
    for case in cases:
        for p in case.entailed:
            hyp = v3.verbalize(p.relation_type, _node(case, p.from_ref), _node(case, p.to_ref))
            assert hyp != v3.UNSUPPORTED_FOR_NLI, (
                f"{case.case_id}: positive {p.from_ref}-{p.relation_type}->{p.to_ref} "
                "unverbalizable — freeze-контракт нарушен")
            examples.append(NliExampleV3(
                case_id=case.case_id, split=case.split, premise=case.text, hypothesis=hyp,
                entailed=True, relation_type=p.relation_type, from_ref=p.from_ref, to_ref=p.to_ref))
        for n in case.not_entailed:
            hyp = v3.verbalize(n.relation_type, _node(case, n.from_ref), _node(case, n.to_ref))
            assert hyp != v3.UNSUPPORTED_FOR_NLI, (
                f"{case.case_id}: negative {n.from_ref}-{n.relation_type}->{n.to_ref} "
                "unverbalizable — freeze-контракт нарушен")
            examples.append(NliExampleV3(
                case_id=case.case_id, split=case.split, premise=case.text, hypothesis=hyp,
                entailed=False, relation_type=n.relation_type, from_ref=n.from_ref, to_ref=n.to_ref))
    return examples

examples = build_examples_v3()
calib = [e for e in examples if e.split == "calibration"]
holdout = [e for e in examples if e.split == "final_holdout"]
calib_case_ids = sorted({e.case_id for e in calib})

print(f"R4.6.F1.2 v3 dataset: {len(examples)} примеров всего "
      f"(freeze commit f8e32a576297d04c90b3bfb4fd2fdf7f1d1c4eb7)")
print(f"  calibration: {len(calib)} примеров, {len(calib_case_ids)} кейсов, "
      f"positive={sum(e.entailed for e in calib)}")
print(f"  final_holdout: {len(holdout)} примеров, "
      f"{len(set(e.case_id for e in holdout))} кейсов, "
      f"positive={sum(e.entailed for e in holdout)}")


def score(model_name: str, exs) -> tuple[list[float], dict]:
    t0 = time.monotonic()
    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    model.eval()
    load_s = time.monotonic() - t0
    id2label = model.config.id2label
    entail_idx = next(i for i, label in id2label.items() if label.lower().startswith("entail"))

    probs: list[float] = []
    t0 = time.monotonic()
    with torch.no_grad():
        for ex in exs:
            inputs = tokenizer(ex.premise, ex.hypothesis, return_tensors="pt", truncation=True)
            logits = model(**inputs).logits[0]
            p = torch.softmax(logits, dim=-1)[entail_idx].item()
            probs.append(p)
    infer_s = time.monotonic() - t0
    peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

    stats = {
        "id2label": dict(id2label), "load_s": load_s, "infer_s": infer_s,
        "throughput": len(exs) / infer_s if infer_s else float("nan"),
        "peak_rss_mb": peak_rss_mb, "num_parameters": model.num_parameters(),
    }
    del model, tokenizer
    return probs, stats


def best_threshold(subset_examples, subset_probs, min_precision=0.90):
    """Максимизирует recall при precision >= 0.90 (product gate).
    `None`, если недостижимо на этом наборе — честно, не подменяется."""
    candidates = sorted(set(subset_probs))
    best = None
    for t in candidates:
        tp = sum(1 for e, p in zip(subset_examples, subset_probs) if p >= t and e.entailed)
        fp = sum(1 for e, p in zip(subset_examples, subset_probs) if p >= t and not e.entailed)
        fn = sum(1 for e, p in zip(subset_examples, subset_probs) if p < t and e.entailed)
        if tp + fp == 0:
            continue
        precision = tp / (tp + fp)
        if precision >= min_precision:
            recall = tp / (tp + fn) if (tp + fn) else 0.0
            if best is None or recall > best[1]:
                best = (t, recall)
    return best[0] if best else None


def confusion(exs, probs, threshold) -> dict:
    tp = fp = tn = fn = 0
    for e, p in zip(exs, probs):
        predicted = p >= threshold
        if e.entailed and predicted:
            tp += 1
        elif e.entailed and not predicted:
            fn += 1
        elif not e.entailed and predicted:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")
    specificity = tn / (tn + fp) if (tn + fp) else float("nan")
    fpr = fp / (fp + tn) if (fp + tn) else float("nan")
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall,
            "f1": f1, "specificity": specificity, "fpr": fpr}


def loocv_within_calibration(calib_exs, calib_probs) -> dict:
    tp = fp = tn = fn = 0
    fold_thresholds = []
    unreachable_folds = []
    for held_out in calib_case_ids:
        cal_e, cal_p, ho_e, ho_p = [], [], [], []
        for e, p in zip(calib_exs, calib_probs):
            (ho_e if e.case_id == held_out else cal_e).append(e)
            (ho_p if e.case_id == held_out else cal_p).append(p)
        threshold = best_threshold(cal_e, cal_p)
        if threshold is None:
            unreachable_folds.append(held_out)
            threshold = max(cal_p) + 1.0
        fold_thresholds.append((held_out, threshold))
        fold_result = confusion(ho_e, ho_p, threshold)
        tp += fold_result["tp"]; fp += fold_result["fp"]
        tn += fold_result["tn"]; fn += fold_result["fn"]
    precision = tp / (tp + fp) if (tp + fp) else float("nan")
    recall = tp / (tp + fn) if (tp + fn) else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else float("nan")
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn, "precision": precision, "recall": recall,
            "f1": f1, "fold_thresholds": fold_thresholds, "unreachable_folds": unreachable_folds}


def auroc(labels: list[bool], scores: list[float]) -> float:
    paired = sorted(zip(scores, labels))
    total = len(paired)
    ranks = [0.0] * total
    i = 0
    while i < total:
        j = i
        while j + 1 < total and paired[j + 1][0] == paired[i][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    n_pos = sum(1 for _, l in paired if l)
    n_neg = total - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    sum_ranks_pos = sum(r for r, (_, l) in zip(ranks, paired) if l)
    return (sum_ranks_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def average_precision(labels: list[bool], scores: list[float]) -> float:
    order = sorted(range(len(scores)), key=lambda idx: -scores[idx])
    n_pos = sum(labels)
    if n_pos == 0:
        return float("nan")
    tp = fp = 0
    ap = 0.0
    prev_recall = 0.0
    for idx in order:
        if labels[idx]:
            tp += 1
        else:
            fp += 1
        precision = tp / (tp + fp)
        recall = tp / n_pos
        ap += precision * (recall - prev_recall)
        prev_recall = recall
    return ap


CANDIDATES = [
    "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7",
    "cointegrated/rubert-base-cased-nli-threeway",
]

for model_name in CANDIDATES:
    print(f"\n########## {model_name} ##########")
    all_probs, stats = score(model_name, examples)
    calib_probs = all_probs[:len(calib)]
    holdout_probs = all_probs[len(calib):]
    assert len(calib_probs) == len(calib) and len(holdout_probs) == len(holdout)

    print(f"  id2label: {stats['id2label']}, num_parameters: {stats['num_parameters']:,}")
    print(f"  load_time={stats['load_s']:.1f}с inference_time={stats['infer_s']:.1f}с "
          f"throughput={stats['throughput']:.1f} пар/с peak_rss={stats['peak_rss_mb']:.0f}MB "
          "(peak_rss — нарастающим итогом с начала процесса)")

    loocv_result = loocv_within_calibration(calib, calib_probs)
    print("  ---- шаг 1: LOOCV внутри calibration (16 фолдов) — sanity-check ----")
    print(f"  TP={loocv_result['tp']} FP={loocv_result['fp']} TN={loocv_result['tn']} FN={loocv_result['fn']}")
    print(f"  precision={loocv_result['precision']:.3f} recall={loocv_result['recall']:.3f} "
          f"F1={loocv_result['f1']:.3f}")
    if loocv_result["unreachable_folds"]:
        print(f"  ВНИМАНИЕ: gate precision>=0.90 недостижим на calibration для фолдов: "
              f"{loocv_result['unreachable_folds']}")

    final_threshold = best_threshold(calib, calib_probs)
    print(f"\n  ---- шаг 2: финальный threshold на ВСЕХ {len(calib)} calibration-примерах ----")
    if final_threshold is None:
        print("  ВНИМАНИЕ: precision>=0.90 недостижим ни на одном threshold на всём calibration set")
        final_threshold = max(calib_probs) + 1.0
    else:
        print(f"  final_threshold={final_threshold:.4f} (NLI probability, НЕ путать с product gate 0.90)")

    print(f"\n  ---- шаг 3: ОДНОКРАТНОЕ применение final_threshold к final_holdout "
          f"({len(holdout)} примеров, {len(set(e.case_id for e in holdout))} кейсов) — ОТЧЁТНЫЙ РЕЗУЛЬТАТ ----")
    holdout_result = confusion(holdout, holdout_probs, final_threshold)
    print(f"  TP={holdout_result['tp']} FP={holdout_result['fp']} "
          f"TN={holdout_result['tn']} FN={holdout_result['fn']}")
    print(f"  typed relation precision (final_holdout)={holdout_result['precision']:.3f} "
          f"recall={holdout_result['recall']:.3f} F1={holdout_result['f1']:.3f} "
          f"specificity={holdout_result['specificity']:.3f} FPR={holdout_result['fpr']:.3f}")

    calib_labels = [e.entailed for e in calib]
    holdout_labels = [e.entailed for e in holdout]
    print(f"\n  AUROC calibration={auroc(calib_labels, calib_probs):.3f} "
          f"AUPRC calibration={average_precision(calib_labels, calib_probs):.3f}")
    print(f"  AUROC final_holdout={auroc(holdout_labels, holdout_probs):.3f} "
          f"AUPRC final_holdout={average_precision(holdout_labels, holdout_probs):.3f}")

    precision_is_nan = holdout_result["precision"] != holdout_result["precision"]  # NaN != NaN
    gate_pass = (not precision_is_nan) and holdout_result["precision"] >= 0.90
    print(f"\n  GATE (typed precision>=0.90 на final_holdout, без коллапса recall): "
          f"{'PASS' if gate_pass else 'FAIL'} (precision={holdout_result['precision']:.3f}, "
          f"recall={holdout_result['recall']:.3f})")

print("\nПродуктовый гейт (владелец): typed relation precision >= 0.90 на final_holdout "
      "БЕЗ коллапса recall, порог подобран ТОЛЬКО на calibration. "
      "Go/no-go к R4.6.F2 — решение владельца по этим цифрам.")
PYEOF

if [ "$diag_rc" -ne 0 ]; then
  echo "::error::бенчмарк завершился с кодом $diag_rc"
fi
exit "$diag_rc"
