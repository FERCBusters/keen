from pathlib import Path
import shutil
import subprocess
import pytest


def test_framework_bulk_selection():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is required for the UI regression check')
    subprocess.run([node, str(Path(__file__).with_name('framework_selection_ui.cjs'))], check=True)
