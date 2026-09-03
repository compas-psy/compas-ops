"""R4.5.4 (владелец 03.09.2026): peak_rss_mb/peak_cpu_percent обязаны
быть максимумом по сэмплам за весь прогон кандидата, а не одиночным
снимком `docker stats` после того, как инференс уже закончился —
единственный снимок ПОСЛЕ работы почти всегда близок к простою
(модель выгружена/RSS уже осела), то есть систематически занижает
пиковую нагрузку, а не просто "иногда промахивается".

Раньше эта логика жила только внутри bash-скрипта (одна строка `docker
stats --no-stream` в конце run_candidate()) и не была protестирована.
Здесь — та же арифметика, но как импортируемая функция: bash-скрипт
теперь только собирает сырые сэмплы (TSV-строки) в background-цикле на
всё время кандидата и передаёт их сюда через stdin, вычисление максимума
покрыто тестами.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from dataclasses import dataclass

_SIZE_RE = re.compile(r"([\d.]+)\s*([A-Za-z]+)")
_SIZE_MULT = {
    "B": 1e-6, "KB": 1e-3, "KiB": 1.048576e-3 * 1000 / 1024,
    "MB": 1.0, "MiB": 1.048576, "GB": 1000.0, "GiB": 1073.741824,
}


def parse_size_mb(text: str) -> float:
    """`docker stats`-стиль размер ("141.5MiB", "4.7GB") -> мегабайты."""
    m = _SIZE_RE.match(text.strip())
    if not m:
        raise ValueError(f"не удалось разобрать размер: {text!r}")
    value, unit = float(m.group(1)), m.group(2)
    return round(value * _SIZE_MULT.get(unit, 1.0), 3)


def parse_percent(text: str) -> float:
    return float(text.strip().rstrip("%"))


@dataclass(frozen=True)
class ResourceSample:
    timestamp: float
    rss_mb: float
    cpu_percent: float
    host_available_ram_mb: float
    swap_used_mb: float


@dataclass(frozen=True)
class PeakStats:
    peak_rss_mb: float
    peak_cpu_percent: float
    min_host_available_ram_mb: float
    swap_before_mb: float
    swap_peak_mb: float
    swap_after_mb: float
    samples_count: int


def parse_sample_line(line: str) -> ResourceSample:
    """Одна строка sampler'а: timestamp, сырой RSS с юнитом (docker
    stats MemUsage до "/"), сырой CPU% с юнитом, host available RAM
    (МБ, без юнита — уже из `free -m`), swap used (МБ, без юнита) —
    через табуляцию."""
    parts = line.rstrip("\n").split("\t")
    if len(parts) != 5:
        raise ValueError(f"ожидалось 5 полей через таб, получено {len(parts)}: {line!r}")
    ts, raw_rss, raw_cpu, avail, swap = parts
    return ResourceSample(
        timestamp=float(ts),
        rss_mb=parse_size_mb(raw_rss),
        cpu_percent=parse_percent(raw_cpu),
        host_available_ram_mb=float(avail),
        swap_used_mb=float(swap),
    )


def compute_peak_stats(samples: list[ResourceSample]) -> PeakStats:
    """R4.5.4: peak = max(sampled), не последний/единственный сэмпл.
    Пустой список сэмплов — ошибка конфигурации sampler'а (не запустился
    или не собрал ни одной точки), не «пик = 0»."""
    if not samples:
        raise ValueError("нет ни одного сэмпла — sampler не запустился или не собрал данных")
    return PeakStats(
        peak_rss_mb=max(s.rss_mb for s in samples),
        peak_cpu_percent=max(s.cpu_percent for s in samples),
        min_host_available_ram_mb=min(s.host_available_ram_mb for s in samples),
        swap_before_mb=samples[0].swap_used_mb,
        swap_peak_mb=max(s.swap_used_mb for s in samples),
        swap_after_mb=samples[-1].swap_used_mb,
        samples_count=len(samples),
    )


def compute_peak_stats_from_lines(lines: list[str]) -> PeakStats:
    samples = [parse_sample_line(line) for line in lines if line.strip()]
    return compute_peak_stats(samples)


def _cli_peak_stats(_args: argparse.Namespace) -> None:
    stats = compute_peak_stats_from_lines(sys.stdin.readlines())
    print(json.dumps(dataclasses.asdict(stats), indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("peak-stats", help="читает TSV-сэмплы из stdin, печатает PeakStats JSON")
    args = parser.parse_args()
    if args.command == "peak-stats":
        _cli_peak_stats(args)


if __name__ == "__main__":
    main()
