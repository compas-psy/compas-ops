"""Замер моделей GigaAM для P8.5.3 (голос → текст, §14.7).

Тот же принцип, что уже применён в `embed_benchmark.py` (§33 запрещает
фиксировать модель без замера на живом сервере — офлайн-песочница
разработки не подходит, HuggingFace/GitHub git-install недоступны её
egress-политикой). Каждый кандидат гоняется в ОТДЕЛЬНОМ подпроцессе —
torch не гарантированно освобождает память между загрузками моделей
внутри одного процесса.

Меряется:
  1. Память: VmRSS до и после загрузки модели.
  2. Латентность: время загрузки + время транскрипции одного тестового
     файла.
  3. Смысл: сам текст транскрипции выводится как есть — для короткого
     синтетического файла (см. ниже) это не автоматическая метрика
     (WER), а визуальная проверка "модель вообще узнаёт русскую речь
     разумно", тот же честно помеченный статус, что был у "смысла" в
     embed_benchmark.py.

Тестовый файл НЕ генерируется этим скриптом: реальной человеческой речи
здесь взять неоткуда (нет TTS/микрофона в контейнере), а синтетическая
речь через espeak-ng генерируется ВНЕ образа воркера, на раннере
GitHub Actions (deploy.yml::gigaam-benchmark) — тестовый ассет, не
часть рантайм-образа. Путь к .wav передаётся аргументом.

Запуск одной модели: python3 -m helm_core.knowledge.gigaam_benchmark --model NAME --audio /path/to/test.wav
Запуск всех кандидатов: python3 -m helm_core.knowledge.gigaam_benchmark --audio /path/to/test.wav
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

#: Короткие имена из gigaam._MODEL_HASHES (проверено чтением исходника
#: репозитория, не по карточке/README) — расширяются библиотекой до
#: версии v3. e2e_* варианты добавляют пунктуацию/нормализацию текста —
#: важно для читаемости в базе знаний, не только для скорости.
CANDIDATES = ["ctc", "rnnt", "e2e_ctc", "e2e_rnnt"]


def _vm_rss_kb() -> int:
    with open("/proc/self/status", encoding="utf-8") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    return -1


def _run_one(model_name: str, audio_path: str) -> dict:
    rss_before = _vm_rss_kb()
    t0 = time.monotonic()
    import gigaam
    model = gigaam.load_model(model_name)
    load_seconds = time.monotonic() - t0
    rss_after_load = _vm_rss_kb()

    t0 = time.monotonic()
    text = model.transcribe(audio_path)
    transcribe_seconds = time.monotonic() - t0
    rss_after_inference = _vm_rss_kb()

    return {
        "model": model_name,
        "load_seconds": round(load_seconds, 2),
        "rss_before_mb": round(rss_before / 1024, 1),
        "rss_after_load_mb": round(rss_after_load / 1024, 1),
        "rss_after_inference_mb": round(rss_after_inference / 1024, 1),
        "transcribe_seconds": round(transcribe_seconds, 3),
        "text": text,
    }


def _run_all(audio_path: str) -> None:
    results = []
    for name in CANDIDATES:
        print(f"=== {name} ===", flush=True)
        proc = subprocess.run(
            [sys.executable, "-m", "helm_core.knowledge.gigaam_benchmark",
            "--model", name, "--audio", audio_path],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            print(f"ПРОВАЛ: {proc.stderr[-4000:]}", flush=True)
            results.append({"model": name, "error": proc.stderr[-2000:]})
            continue
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    print("\n=== Сводная таблица ===", flush=True)
    header = f"{'модель':<10} {'RSS МБ':>8} {'загрузка с':>11} {'транскрипция с':>15}"
    print(header, flush=True)
    for r in results:
        if "error" in r:
            print(f"{r['model']:<10} ОШИБКА — см. вывод выше", flush=True)
            continue
        print(
            f"{r['model']:<10} {r['rss_after_inference_mb']:>8.1f} "
            f"{r['load_seconds']:>11.2f} {r['transcribe_seconds']:>15.3f}",
            flush=True,
        )
    print(
        "\nТекст транскрипции для каждой модели — см. вывод выше. Файл "
        "синтетический (espeak-ng), не живая речь — оценка смысла здесь "
        "визуальная ('узнаёт ли модель разумные русские слова'), не "
        "автоматическая метрика WER.",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    parser.add_argument("--audio", required=True)
    args = parser.parse_args()

    if args.model:
        result = _run_one(args.model, args.audio)
        print(json.dumps(result, ensure_ascii=False))
        return

    _run_all(args.audio)


if __name__ == "__main__":
    main()
