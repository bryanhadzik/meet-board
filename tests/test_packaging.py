"""Every app module must be listed in pyproject's wheel only-include, or a
wheel build silently ships without it (the PyInstaller exe follows imports,
so the Windows build would never notice)."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_every_module_is_packaged():
    text = open(os.path.join(ROOT, 'pyproject.toml'), encoding='utf-8').read()
    block = re.search(r'only-include\s*=\s*\[(.*?)\]', text, re.S).group(1)
    listed = set(re.findall(r'"([^"]+)"', block))
    modules = {os.path.basename(p) for p in glob.glob(os.path.join(ROOT, '*.py'))}
    assert modules - listed == set()
