from pathlib import Path
import socket
import sys
import os
import pytest

from app import runtime
from app.desktop import bind_local_socket
from app.desktop import InstanceLock
from app.main import create_app


def test_source_default_stays_in_existing_project_data(monkeypatch):
    monkeypatch.delenv('TUTOR_DB', raising=False)
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert runtime.database_path() == runtime.ROOT / 'data/tutor.sqlite3'


def test_frozen_data_is_per_user_outside_install_directory(tmp_path, monkeypatch):
    monkeypatch.delenv('TUTOR_DB', raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    assert runtime.database_path() == tmp_path / 'TutorAssistant/data/tutor.sqlite3'


def test_explicit_test_database_controls_worker_diagnostics(tmp_path, monkeypatch):
    target = tmp_path / 'isolated' / 'private.sqlite3'
    monkeypatch.setenv('TUTOR_DB', str(target))
    assert runtime.database_path() == target
    assert runtime.data_directory() == target.parent


def test_explicit_database_argument_remains_supported(tmp_path, monkeypatch):
    monkeypatch.setenv('TUTOR_DB', str(tmp_path / 'other.sqlite3'))
    target = tmp_path / 'explicit.sqlite3'
    assert Path(create_app(target).state.store.path) == target
    assert not (tmp_path / 'other.sqlite3').exists()


def test_busy_port_does_not_attach_to_an_unrelated_app():
    first = bind_local_socket(18765)
    try:
        second = bind_local_socket(first.getsockname()[1])
        try:
            assert second.getsockname()[0] == '127.0.0.1'
            assert second.getsockname()[1] != first.getsockname()[1]
        finally:
            second.close()
    finally:
        first.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows byte-lock lifecycle')
def test_duplicate_desktop_instance_waits_for_existing_process(tmp_path):
    first, second = InstanceLock(tmp_path), InstanceLock(tmp_path)
    try:
        assert first.acquire()
        assert not second.acquire()
    finally:
        first.release()
    assert second.acquire()
    second.release()
