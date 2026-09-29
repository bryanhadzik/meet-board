"""Where things live, whether running from source or as a PyInstaller exe.

BUNDLE_DIR holds read-only resources shipped with the app (templates, static).
DATA_DIR holds files the app writes (settings.json). When frozen, that is the
folder containing meet-board.exe, so settings survive updates.
"""
import os
import sys

FROZEN = getattr(sys, 'frozen', False)

if FROZEN:
    BUNDLE_DIR = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
    DATA_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = BUNDLE_DIR


def bundle_path(*parts):
    return os.path.join(BUNDLE_DIR, *parts)


def data_path(*parts):
    return os.path.join(DATA_DIR, *parts)
