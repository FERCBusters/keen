"""The PDF preview child as the API starts it, with ``python -m``.

The module limits its own resources and replaces itself with pdftoppm only
when run as the program; importing it starts nothing. The operating system's
limits and exec are replaced at their boundary.
"""

import os
import resource
import runpy
import sys

from app.render import pdf_preview


def _run(monkeypatch, run_name):
    limits, executed = [], []
    monkeypatch.setattr(resource, "setrlimit", lambda kind, pair: limits.append(kind))
    monkeypatch.setattr(os, "execvp", lambda file, argv: executed.append(argv))
    monkeypatch.setattr(sys, "argv", ["pdf_preview", "in.pdf", "out"])
    runpy.run_path(pdf_preview.__file__, run_name=run_name)
    return limits, executed


def test_run_as_a_program_the_child_limits_itself_then_runs_pdftoppm(monkeypatch):
    limits, executed = _run(monkeypatch, "__main__")

    assert limits == [
        resource.RLIMIT_AS,
        resource.RLIMIT_CPU,
        resource.RLIMIT_FSIZE,
        resource.RLIMIT_CORE,
    ]
    (argv,) = executed
    assert argv[0] == "pdftoppm" and argv[-2:] == ["in.pdf", "out"]


def test_importing_the_child_module_limits_and_runs_nothing(monkeypatch):
    assert _run(monkeypatch, "app.render.pdf_preview") == ([], [])
