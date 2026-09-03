"""R4.5.4 (владелец 03.09.2026): peak_rss_mb/peak_cpu_percent обязаны
быть максимумом по сэмплам за весь прогон, не одиночным снимком
`docker stats` после того, как инференс уже закончился.

Регрессия, которую эти тесты обязаны ловить: если бы кто-то снова
подставил «последний сэмпл» вместо настоящего максимума (тот самый
баг, который был в r4-golden-benchmark.sh), тест с пиком ПОСЕРЕДИНЕ
последовательности сразу провалится — «пик = последняя точка»
неотличим от «пик = максимум» только на монотонных данных, поэтому
монотонной последовательности среди фикстур ниже нет намеренно."""

from __future__ import annotations

import pytest

from helm_core.knowledge.resource_sampling import (
    PeakStats,
    ResourceSample,
    compute_peak_stats,
    compute_peak_stats_from_lines,
    parse_percent,
    parse_sample_line,
    parse_size_mb,
)


def _sample(ts, rss_mb, cpu_percent, avail_mb, swap_mb) -> ResourceSample:
    return ResourceSample(timestamp=ts, rss_mb=rss_mb, cpu_percent=cpu_percent,
                          host_available_ram_mb=avail_mb, swap_used_mb=swap_mb)


class TestParseSizeMb:
    @pytest.mark.parametrize("text,expected", [
        ("141.5MiB", 148.35),
        ("4.7GB", 4700.0),
        ("2000000B", 2.0),
        ("2KiB", 0.002),
    ])
    def test_known_units_convert_correctly(self, text, expected):
        assert parse_size_mb(text) == pytest.approx(expected, rel=1e-3)

    def test_garbage_input_raises_not_silently_zero(self):
        with pytest.raises(ValueError):
            parse_size_mb("garbage")


class TestParsePercent:
    def test_strips_percent_sign(self):
        assert parse_percent("12.34%") == pytest.approx(12.34)

    def test_zero_percent_is_not_missing(self):
        # 0.0% реально означает "не измерено ядром никакой нагрузки",
        # не "не измерено вовсе" — эта функция обязана вернуть 0.0, не
        # бросить исключение и не завернуть в None (иначе тот же класс
        # бага, что peak_rss_mb=None -> 0.0 в _ranking_key, повторится
        # уже на этапе парсинга).
        assert parse_percent("0.0%") == 0.0


class TestParseSampleLine:
    def test_parses_five_tab_separated_fields(self):
        sample = parse_sample_line("100.5\t141.5MiB\t12.3%\t2048\t511\n")
        assert sample == _sample(100.5, pytest.approx(148.35, rel=1e-3), pytest.approx(12.3),
                                 2048.0, 511.0)

    def test_wrong_field_count_raises(self):
        with pytest.raises(ValueError):
            parse_sample_line("100.5\t141.5MiB\t12.3%\n")


class TestComputePeakStats:
    def test_empty_samples_raises_not_defaults_to_zero(self):
        # R4.4e (missing != 0.0) — та же дисциплина здесь: sampler,
        # который не собрал ни одной точки, обязан провалить гейт, а не
        # тихо выдать peak_rss_mb=0.0, что выглядело бы как «идеально
        # мало памяти».
        with pytest.raises(ValueError):
            compute_peak_stats([])

    def test_peak_is_the_true_maximum_not_the_last_sample(self):
        # Пик — ВТОРОЙ из трёх сэмплов, не последний. Старый баг (один
        # snapshot docker stats ПОСЛЕ инференса) эквивалентен «взять
        # последний сэмпл» — на этих данных он дал бы 100.0/10.0, а не
        # реальный пик 500.0/80.0.
        samples = [
            _sample(0.0, rss_mb=200.0, cpu_percent=20.0, avail_mb=4000.0, swap_mb=0.0),
            _sample(2.0, rss_mb=500.0, cpu_percent=80.0, avail_mb=3200.0, swap_mb=100.0),
            _sample(4.0, rss_mb=100.0, cpu_percent=10.0, avail_mb=3900.0, swap_mb=50.0),
        ]
        stats = compute_peak_stats(samples)
        assert stats.peak_rss_mb == 500.0
        assert stats.peak_cpu_percent == 80.0

    def test_min_host_available_ram_is_the_true_minimum(self):
        samples = [
            _sample(0.0, rss_mb=1.0, cpu_percent=1.0, avail_mb=4000.0, swap_mb=0.0),
            _sample(2.0, rss_mb=1.0, cpu_percent=1.0, avail_mb=1500.0, swap_mb=0.0),
            _sample(4.0, rss_mb=1.0, cpu_percent=1.0, avail_mb=3900.0, swap_mb=0.0),
        ]
        stats = compute_peak_stats(samples)
        assert stats.min_host_available_ram_mb == 1500.0

    def test_swap_before_peak_after_are_first_max_last_not_all_equal(self):
        samples = [
            _sample(0.0, rss_mb=1.0, cpu_percent=1.0, avail_mb=1.0, swap_mb=100.0),
            _sample(2.0, rss_mb=1.0, cpu_percent=1.0, avail_mb=1.0, swap_mb=900.0),
            _sample(4.0, rss_mb=1.0, cpu_percent=1.0, avail_mb=1.0, swap_mb=300.0),
        ]
        stats = compute_peak_stats(samples)
        assert stats.swap_before_mb == 100.0
        assert stats.swap_peak_mb == 900.0
        assert stats.swap_after_mb == 300.0

    def test_single_sample_is_a_degenerate_but_valid_peak(self):
        stats = compute_peak_stats([_sample(0.0, 50.0, 5.0, 1000.0, 0.0)])
        assert stats.peak_rss_mb == 50.0
        assert stats.samples_count == 1


class TestComputePeakStatsFromLines:
    def test_end_to_end_from_raw_tsv_lines_with_peak_in_the_middle(self):
        lines = [
            "0.0\t200MiB\t20.0%\t4000\t0\n",
            "2.0\t500MiB\t80.0%\t3200\t100\n",
            "4.0\t100MiB\t10.0%\t3900\t50\n",
        ]
        stats = compute_peak_stats_from_lines(lines)
        assert isinstance(stats, PeakStats)
        assert stats.peak_rss_mb == pytest.approx(524.288, rel=1e-3)  # 500 MiB -> MB

    def test_blank_lines_are_ignored_not_counted_as_samples(self):
        lines = ["0.0\t100MiB\t10.0%\t1000\t0\n", "\n", "   \n", "2.0\t200MiB\t20.0%\t900\t0\n"]
        stats = compute_peak_stats_from_lines(lines)
        assert stats.samples_count == 2
