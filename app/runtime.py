"""Separate installed resources from each user's writable data."""
import os
import sys
from pathlib import Path

VERSION = '0.2.4-rc.1'
ROOT = Path(__file__).resolve().parent.parent


def data_directory():
    if os.environ.get('TUTOR_DB'):
        return Path(os.environ['TUTOR_DB']).expanduser().resolve().parent
    if getattr(sys, 'frozen', False):
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'TutorAssistant' / 'data'
    return ROOT / 'data'


def database_path():
    return Path(os.environ['TUTOR_DB']).expanduser().resolve() if os.environ.get('TUTOR_DB') else data_directory() / 'tutor.sqlite3'
