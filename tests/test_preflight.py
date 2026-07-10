import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import preflight  # noqa: E402
import robotctl  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# preflight.py
# --------------------------------------------------------------------------


def test_check_python_passes_when_version_meets_minimum() -> None:
    result = preflight.check_python(min_version=(3, 8))
    assert result.status == "OK"
    assert result.name == "Python"


def test_check_python_fails_when_version_below_minimum() -> None:
    result = preflight.check_python(min_version=(99, 0))
    assert result.status == "NG"
    assert result.required is True


def test_check_git_ok_against_real_repo() -> None:
    result = preflight.check_git(PROJECT_ROOT)
    assert result.status == "OK"
    assert "@" in result.detail


def test_check_git_ng_against_non_repo(tmp_path: Path) -> None:
    result = preflight.check_git(tmp_path)
    assert result.status == "NG"


def test_check_i2c_ok_when_device_exists(tmp_path: Path) -> None:
    dev = tmp_path / "i2c-1"
    dev.touch()
    result = preflight.check_i2c(str(dev))
    assert result.status == "OK"


def test_check_i2c_ng_when_device_missing(tmp_path: Path) -> None:
    result = preflight.check_i2c(str(tmp_path / "does-not-exist"))
    assert result.status == "NG"


def test_check_serial_ports_ok_when_all_present(tmp_path: Path) -> None:
    front = tmp_path / "ttyUSB-Front"
    back = tmp_path / "ttyUSB-Back"
    front.touch()
    back.touch()
    result = preflight.check_serial_ports((str(front), str(back)))
    assert result.status == "OK"


def test_check_serial_ports_ng_lists_missing(tmp_path: Path) -> None:
    front = tmp_path / "ttyUSB-Front"
    front.touch()
    missing = tmp_path / "ttyUSB-Back"
    result = preflight.check_serial_ports((str(front), str(missing)))
    assert result.status == "NG"
    assert str(missing) in result.detail


def test_check_bmx055_skips_when_smbus2_not_installed() -> None:
    # smbus2 is a pi-sensors-only extra; the dev venv used for this test suite
    # does not install it, so the check must degrade to SKIP rather than error.
    result = preflight.check_bmx055()
    assert result.status == "SKIP"


def test_check_logs_writable_ok_on_tmp_path(tmp_path: Path) -> None:
    result = preflight.check_logs_writable(tmp_path / "logs")
    assert result.status == "OK"


def test_check_logs_writable_ng_on_read_only_dir(tmp_path: Path) -> None:
    target = tmp_path / "readonly"
    target.mkdir()
    target.chmod(0o500)
    try:
        if os.access(target, os.W_OK):
            pytest.skip("running with privileges that bypass directory permissions")
        result = preflight.check_logs_writable(target)
        assert result.status == "NG"
    finally:
        target.chmod(0o700)


def test_check_disk_free_ok_with_tiny_minimum(tmp_path: Path) -> None:
    result = preflight.check_disk_free(tmp_path, min_free_gb=0.001)
    assert result.status == "OK"


def test_check_disk_free_ng_with_huge_minimum(tmp_path: Path) -> None:
    result = preflight.check_disk_free(tmp_path, min_free_gb=10**9)
    assert result.status == "NG"


def test_format_results_lines_include_status_and_name() -> None:
    results = [
        preflight.CheckResult("Python", "OK", "3.14.0"),
        preflight.CheckResult("BMX055", "SKIP", "smbus2 not installed — run on Pi"),
    ]
    output = preflight.format_results(results)
    assert "[OK] Python: 3.14.0" in output
    assert "[SKIP] BMX055:" in output


def test_main_exit_code_zero_when_only_optional_ng(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_results = [
        preflight.CheckResult("Python", "OK", "3.14.0"),
        preflight.CheckResult("API health", "NG", "server down", required=False),
    ]
    monkeypatch.setattr(preflight, "run_preflight", lambda **kwargs: fake_results)
    assert preflight.main([]) == 0


def test_main_exit_code_one_when_required_ng(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_results = [
        preflight.CheckResult("Python", "NG", "too old"),
        preflight.CheckResult("API health", "NG", "server down", required=False),
    ]
    monkeypatch.setattr(preflight, "run_preflight", lambda **kwargs: fake_results)
    assert preflight.main([]) == 1


def test_run_preflight_returns_all_checks() -> None:
    results = preflight.run_preflight(project_root=PROJECT_ROOT)
    names = {result.name for result in results}
    assert {"Python", "Git", "I2C bus", "BMX055", "Serial ports", "Disk free"} <= names


# --------------------------------------------------------------------------
# robotctl.py
# --------------------------------------------------------------------------


def test_normalize_base_url_adds_scheme_when_missing() -> None:
    assert robotctl.normalize_base_url("127.0.0.1:8000") == "http://127.0.0.1:8000"


def test_normalize_base_url_keeps_existing_scheme() -> None:
    assert robotctl.normalize_base_url("https://example.com:8000/") == "https://example.com:8000"


def test_human_bytes_formats_kilobytes() -> None:
    assert robotctl.human_bytes(2048) == "2.0 KB"


def test_human_bytes_formats_bytes() -> None:
    assert robotctl.human_bytes(512) == "512 B"


def test_human_duration_formats_minutes_and_seconds() -> None:
    assert robotctl.human_duration(125) == "2m5s"
