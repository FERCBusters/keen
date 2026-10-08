"""Exercise async history selection using the shipped UI function in Node."""
from pathlib import Path
import shutil
import subprocess
import pytest


def test_rule_history_ui_state_and_async_race():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is required for the UI regression check")
    subprocess.run([node, str(Path(__file__).with_name("rule_revision_ui.cjs"))], check=True)
