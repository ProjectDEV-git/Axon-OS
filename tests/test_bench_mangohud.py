"""The MangoHud benchmark summary and comparison tool."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "bench_mangohud", ROOT / "scripts" / "bench-mangohud.py"
)
bench = importlib.util.module_from_spec(spec)
sys.modules["bench_mangohud"] = bench  # dataclasses look their module up here
spec.loader.exec_module(bench)

HEADER = "os,cpu,gpu,ram,kernel,driver,cpuscheduler\nUbuntu,Ryzen,RX 7800,32,6.8,mesa,none\n"


def write_log(path: Path, frametimes, columns="fps,frametime,cpu_load,gpu_load,elapsed"):
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [f"{1000 / ft:.1f},{ft},10,90,{i}" for i, ft in enumerate(frametimes)]
    path.write_text(HEADER + columns + "\n" + "\n".join(rows) + "\n")
    return path


@pytest.mark.unit
def test_constant_frame_rate(tmp_path):
    log = write_log(tmp_path / "run1.csv", [10.0] * 500)
    run = bench.summarize(log)
    assert run.frames == 500
    assert run.avg_fps == pytest.approx(100.0)
    assert run.low_1pct_fps == pytest.approx(100.0)


@pytest.mark.unit
def test_stutters_pull_the_1_percent_low_down_but_barely_move_the_average(tmp_path):
    frames = [10.0] * 990 + [100.0] * 10  # 1% of the frames take 100 ms
    run = bench.summarize(write_log(tmp_path / "run.csv", frames))
    assert run.avg_fps == pytest.approx(1000 * 1000 / (9900 + 1000))
    assert run.low_1pct_fps == pytest.approx(10.0)
    assert run.avg_fps > 90


@pytest.mark.unit
def test_header_is_found_after_the_system_information_and_bad_rows_are_skipped(tmp_path):
    log = tmp_path / "run.csv"
    log.write_text(HEADER + "fps,frametime\n100,10\nbroken\n,\n50,20\n0,0\n")
    assert bench.read_frametimes(log) == [10.0, 20.0]


@pytest.mark.unit
@pytest.mark.parametrize("content", ["", "just,some,text\n1,2,3\n", "fps,frametime\n"])
def test_unusable_logs_are_rejected(tmp_path, content):
    log = tmp_path / "bad.csv"
    log.write_text(content)
    with pytest.raises(bench.LogError):
        bench.summarize(log)


@pytest.mark.unit
def test_game_result_uses_the_median_and_flags_few_or_noisy_runs():
    runs = [bench.Run(Path(f"r{i}"), 100, fps, fps / 2) for i, fps in enumerate((60.0, 62.0, 61.0))]
    result = bench.game_result("g", runs)
    assert result.avg_fps == 61.0 and result.low_1pct_fps == 30.5
    assert result.warnings == ()
    assert "only 2 run(s)" in bench.game_result("g", runs[:2]).warnings[0]
    noisy = [
        bench.Run(Path("a"), 1, 60.0, 30.0),
        bench.Run(Path("b"), 1, 70.0, 30.0),
        bench.Run(Path("c"), 1, 61.0, 30.0),
    ]
    assert any("differ" in w for w in bench.game_result("g", noisy).warnings)


def make_results(root: Path, scale: float, games=("alpha", "beta"), runs=3):
    for game in games:
        for n in range(1, runs + 1):
            write_log(root / game / f"run{n}.csv", [10.0 / scale] * 300)


@pytest.mark.unit
def test_compare_reports_the_change_per_game(tmp_path, capsys):
    make_results(tmp_path / "stock", 1.0)
    make_results(tmp_path / "axon", 1.1)
    assert (
        bench.main(["compare", str(tmp_path / "stock"), str(tmp_path / "axon"), "--markdown"]) == 0
    )
    out = capsys.readouterr().out
    assert "| alpha | 100.0 | 110.0 | +10.0% |" in out
    assert out.count("| ok |") == 2


@pytest.mark.unit
def test_compare_flags_missing_games_and_too_few_runs(tmp_path, capsys):
    make_results(tmp_path / "stock", 1.0)
    make_results(tmp_path / "axon", 1.0, games=("alpha",), runs=2)
    assert bench.main(["compare", str(tmp_path / "stock"), str(tmp_path / "axon")]) == 0
    out = capsys.readouterr().out
    assert "missing from one side" in out and "after: only 2 run(s)" in out


@pytest.mark.unit
def test_cli_errors_are_exit_codes_not_tracebacks(tmp_path, capsys):
    assert bench.main(["summarize", str(tmp_path / "nope.csv")]) == 1
    assert bench.main(["compare", str(tmp_path), str(tmp_path)]) == 1
    assert "bench-mangohud:" in capsys.readouterr().err


@pytest.mark.unit
def test_benchmark_doc_uses_real_mangohud_keys():
    doc = (ROOT / "docs" / "benchmarks" / "README.md").read_text()
    for key in ("output_folder", "autostart_log", "log_duration", "log_interval"):
        assert key in doc
