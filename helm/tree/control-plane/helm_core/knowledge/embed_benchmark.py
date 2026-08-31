"""Замер embedding-моделей для P8.5.4 остатка (смысловой поиск, §14.12).

Спека (§33) прямо запрещает фиксировать модель без замера на живом
сервере: "P8.5 Knowledge Localizer/GigaAM/local embeddings ...still require
resource/quality benchmark". Этот файл — сам замер, не финальный сервис.
Каждая модель-кандидат гоняется в ОТДЕЛЬНОМ подпроцессе (`--model NAME`):
torch не гарантированно освобождает память между `SentenceTransformer(...)`
внутри одного процесса, и замер RSS одной модели после уже загруженной
другой был бы враньём, выглядящим как число.

Три вещи меряются:
  1. Память: VmRSS процесса до и после загрузки модели (/proc/self/status —
     без лишней зависимости вроде psutil, sentence-transformers её и так не
     тянет).
  2. Латентность: пакет из ~30 синтетических "абзацев" (эмуляция чанков
     ingest) и одиночный текст (эмуляция вопроса на проверке Probe).
  3. Смысловое качество: набор русских пар "вопрос — идентичный по смыслу
     ответ БЕЗ общих словных корней" против отвлекающего текста на другую
     тему — ровно тот разрыв, который лексический Z0/Z1 сейчас не
     перекрывает (probe.py docstring). Это НЕ полноценная IR-оценка на
     golden-set, а минимальная проверка "модель вообще ловит смысл на
     русском" — с честной пометкой в выводе, что это такое.

Запуск одной модели: python3 -m helm_core.knowledge.embed_benchmark --model NAME
Запуск всех кандидатов (по умолчанию): python3 -m helm_core.knowledge.embed_benchmark
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

#: query_prefix/passage_prefix — конвенция семейства E5 (intfloat/*):
#: модель обучена с этими префиксами и без них теряет в качестве (см.
#: карточку модели). У остальных семейств префиксов нет — пустая строка.
CANDIDATES = [
    {"name": "intfloat/multilingual-e5-small", "query_prefix": "query: ", "passage_prefix": "passage: "},
    {"name": "intfloat/multilingual-e5-base", "query_prefix": "query: ", "passage_prefix": "passage: "},
    {"name": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "query_prefix": "", "passage_prefix": ""},
    {"name": "BAAI/bge-m3", "query_prefix": "", "passage_prefix": ""},
]

#: Пары "вопрос — идентичный по смыслу ответ, без общих корней" + один
#: отвлекающий текст на другую тему. Хороший результат: cos(query, positive)
#: заметно выше cos(query, negative) на каждой паре.
TEST_PAIRS = [
    {
        "query": "Сколько стоит месячная подписка?",
        "positive": "Абонентская плата за месяц составляет три тысячи рублей.",
        "negative": "Погода в Москве сегодня пасмурная, возможен дождь.",
    },
    {
        "query": "Как связаться с поддержкой?",
        "positive": "По всем вопросам пишите на почту support@example.com.",
        "negative": "Рецепт борща требует свёклы, капусты и говядины.",
    },
    {
        "query": "Где хранятся резервные копии?",
        "positive": "Бэкапы ежедневно загружаются в облачное хранилище.",
        "negative": "Кот проспал на подоконнике весь день.",
    },
    {
        "query": "Когда был подписан договор?",
        "positive": "Соглашение вступило в силу пятнадцатого марта.",
        "negative": "Новый ноутбук получил более ёмкую батарею.",
    },
]

#: Синтетические "чанки" разной длины — эмуляция ingest-нагрузки, не
#: реальный текст пользователя (в песочнице разработки нет доступа к
#: живой базе, HuggingFace тоже недоступен офлайн — сам замер поэтому
#: возможен только на живом сервере, см. запуск через deploy.yml).
_LOREM = (
    "Второй мозг хранит источники знания по доменам и позволяет находить "
    "ответ без обращения к платной модели, если ответ уже есть в базе. "
)
SYNTHETIC_CHUNKS = [_LOREM * n for n in (1, 2, 4)] * 10  # 30 текстов


def _vm_rss_kb() -> int:
    with open("/proc/self/status", encoding="utf-8") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def _cosine(a, b) -> float:
    import numpy as np
    a, b = np.asarray(a), np.asarray(b)
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(np.dot(a, b) / denom)


def _run_one(name: str, query_prefix: str, passage_prefix: str) -> dict:
    rss_before = _vm_rss_kb()
    t0 = time.monotonic()
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(name, device="cpu")
    load_seconds = time.monotonic() - t0
    dim = model.get_sentence_embedding_dimension()
    rss_after_load = _vm_rss_kb()

    def embed(texts, is_query: bool):
        prefix = query_prefix if is_query else passage_prefix
        return model.encode([prefix + t for t in texts], normalize_embeddings=True)

    # Латентность: пакет (имитация индексации) + одиночный текст (имитация
    # вопроса на живом Probe — латентность одного вызова важна отдельно от
    # пропускной способности пакета).
    t0 = time.monotonic()
    embed(SYNTHETIC_CHUNKS, is_query=False)
    batch_seconds = time.monotonic() - t0

    t0 = time.monotonic()
    embed([SYNTHETIC_CHUNKS[0]], is_query=False)
    single_seconds = time.monotonic() - t0

    rss_after_inference = _vm_rss_kb()

    # Смысловое качество.
    margins = []
    for pair in TEST_PAIRS:
        q = embed([pair["query"]], is_query=True)[0]
        pos = embed([pair["positive"]], is_query=False)[0]
        neg = embed([pair["negative"]], is_query=False)[0]
        sim_pos, sim_neg = _cosine(q, pos), _cosine(q, neg)
        margins.append({"sim_positive": sim_pos, "sim_negative": sim_neg,
                        "correct_order": sim_pos > sim_neg})

    return {
        "model": name,
        "dim": dim,
        "load_seconds": round(load_seconds, 2),
        "rss_before_mb": round(rss_before / 1024, 1),
        "rss_after_load_mb": round(rss_after_load / 1024, 1),
        "rss_after_inference_mb": round(rss_after_inference / 1024, 1),
        "batch_30_seconds": round(batch_seconds, 3),
        "single_text_seconds": round(single_seconds, 3),
        "quality_pairs_correct": sum(1 for m in margins if m["correct_order"]),
        "quality_pairs_total": len(margins),
        "quality_margins": margins,
    }


def _run_all() -> None:
    results = []
    for cand in CANDIDATES:
        print(f"=== {cand['name']} ===", flush=True)
        proc = subprocess.run(
            [sys.executable, "-m", "helm_core.knowledge.embed_benchmark",
            "--model", cand["name"],
            "--query-prefix", cand["query_prefix"],
            "--passage-prefix", cand["passage_prefix"]],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(f"ПРОВАЛ: {proc.stderr[-4000:]}", flush=True)
            results.append({"model": cand["name"], "error": proc.stderr[-2000:]})
            continue
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    print("\n=== Сводная таблица ===", flush=True)
    header = f"{'модель':<55} {'dim':>5} {'RSS МБ':>8} {'загрузка с':>11} {'пакет-30 с':>11} {'1 текст с':>10} {'смысл':>7}"
    print(header, flush=True)
    for r in results:
        if "error" in r:
            print(f"{r['model']:<55} ОШИБКА — см. вывод выше", flush=True)
            continue
        quality = f"{r['quality_pairs_correct']}/{r['quality_pairs_total']}"
        print(
            f"{r['model']:<55} {r['dim']:>5} {r['rss_after_inference_mb']:>8.1f} "
            f"{r['load_seconds']:>11.2f} {r['batch_30_seconds']:>11.3f} "
            f"{r['single_text_seconds']:>10.3f} {quality:>7}",
            flush=True,
        )
    print(
        "\n«смысл» — доля из "
        f"{len(TEST_PAIRS)} пар «перефразированный вопрос без общих корней», "
        "где похожесть на верный ответ выше похожести на отвлекающий текст. "
        "Это минимальная проверка на русском, не полноценная IR-оценка на "
        "golden-set.",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--query-prefix", default="")
    parser.add_argument("--passage-prefix", default="")
    args = parser.parse_args()

    if args.model:
        result = _run_one(args.model, args.query_prefix, args.passage_prefix)
        print(json.dumps(result, ensure_ascii=False))
        return

    _run_all()


if __name__ == "__main__":
    main()
