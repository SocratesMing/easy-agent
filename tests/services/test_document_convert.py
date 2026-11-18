"""Office 转换：目标格式隔离、失败处理及并发输出完整性。"""

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from easy_agent.services import document_convert as dc


@pytest.fixture
def conversion(monkeypatch, tmp_path):
    source = tmp_path / "中文 文档.doc"
    source.write_bytes(b"source")
    monkeypatch.setattr(dc, "CACHE_DIR", tmp_path / "cache with spaces")
    monkeypatch.setattr(dc, "_find_soffice", lambda: "/fake/soffice")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        out = Path(command[command.index("--outdir") + 1])
        target = command[command.index("--convert-to") + 1].split(":", 1)[0]
        (out / f"{source.stem}.{target}").write_bytes(f"complete {target}".encode())
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(dc.subprocess, "run", run)
    return source, calls, run


def test_pdf_wrapper_and_docx_have_separate_caches(conversion):
    source, calls, _ = conversion
    pdf = dc.convert_to_pdf(source)
    docx = dc.convert_office_document(source, "docx")

    assert pdf.read_bytes() == b"complete pdf"
    assert docx.read_bytes() == b"complete docx"
    assert pdf.parent != docx.parent
    assert len(calls) == 2
    assert "docx:Office Open XML Text" in calls[1]
    assert dc.convert_office_document(source, "docx") == docx
    assert len(calls) == 2


def test_valid_cache_does_not_require_installed_converter(conversion, monkeypatch):
    source, calls, _ = conversion
    cached = dc.convert_to_pdf(source)
    monkeypatch.setattr(dc, "_find_soffice", lambda: None)

    assert dc.convert_to_pdf(source) == cached
    assert len(calls) == 1


def test_source_change_invalidates_cache(conversion):
    source, calls, _ = conversion
    old = dc.convert_to_pdf(source)
    stat = source.stat()
    source.write_bytes(b"updated source")
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    new = dc.convert_to_pdf(source)

    assert new != old
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "no_output", "empty_output"])
def test_failed_conversion_leaves_no_cached_output(conversion, monkeypatch, failure):
    source, _, _ = conversion

    def fail(command, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 180)
        if failure == "nonzero":
            raise subprocess.CalledProcessError(1, command, stderr=b"Error: damaged document")
        if failure == "empty_output":
            out = Path(command[command.index("--outdir") + 1])
            (out / f"{source.stem}.docx").touch()
        return subprocess.CompletedProcess(
            command, 0, stdout=b"", stderr=b"Error: source file could not be loaded"
        )

    monkeypatch.setattr(dc.subprocess, "run", fail)
    with pytest.raises(RuntimeError):
        dc.convert_office_document(source, "docx")
    assert not list(dc.CACHE_DIR.rglob("*.docx"))
    assert not list(dc.CACHE_DIR.rglob(".convert-*"))


def test_concurrent_conversions_use_separate_profiles(conversion, monkeypatch):
    source, calls, run = conversion
    barrier = Barrier(2)

    def concurrent_run(command, **kwargs):
        barrier.wait(timeout=5)
        return run(command, **kwargs)

    monkeypatch.setattr(dc.subprocess, "run", concurrent_run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dc.convert_office_document, source, "docx") for _ in range(2)]
        paths = [future.result(timeout=10) for future in futures]

    assert paths[0] == paths[1]
    assert paths[0].read_bytes() == b"complete docx"
    profiles = [next(arg for arg in command if arg.startswith("-env:")) for command in calls]
    assert len(set(profiles)) == 2
    assert all("%20" in profile for profile in profiles)
    assert not list(dc.CACHE_DIR.rglob(".convert-*"))


def test_unsupported_target_is_rejected_before_execution(conversion):
    source, calls, _ = conversion
    with pytest.raises(ValueError):
        dc.convert_office_document(source, "../../unexpected")
    assert not calls


def test_missing_converter_has_actionable_error(conversion, monkeypatch):
    source, _, _ = conversion
    monkeypatch.setattr(dc, "_find_soffice", lambda: None)
    with pytest.raises(RuntimeError, match="LibreOffice"):
        dc.convert_office_document(source, "docx")


@pytest.mark.parametrize("env_name", ["ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"])
@pytest.mark.parametrize("binary", ["soffice.com", "soffice.exe"])
def test_windows_discovers_libreoffice_without_path(tmp_path, monkeypatch, env_name, binary):
    monkeypatch.setattr(dc.sys, "platform", "win32")
    monkeypatch.setattr(dc.shutil, "which", lambda _: None)
    for name in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "Program Files"
    executable = root / "LibreOffice" / "program" / binary
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv(env_name, str(root))

    assert dc._find_soffice() == str(executable)


def test_windows_prefers_console_executable_from_path(monkeypatch):
    monkeypatch.setattr(dc.sys, "platform", "win32")
    monkeypatch.setattr(dc.shutil, "which", lambda name: f"/tools/{name}")

    assert dc._find_soffice() == "/tools/soffice.com"
