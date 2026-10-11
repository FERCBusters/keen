"""Architectural contracts supplement behavioural and assembled-app security tests."""

import ast
from pathlib import Path

from app.api.routing import Router
from app.services.errors import DomainError
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

APP = Path(__file__).resolve().parents[1] / "app"


def test_services_do_not_depend_on_http_adapters():
    violations = []
    for path in (APP / "services").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                if node.module == "app":
                    names.extend(f"app.{alias.name}" for alias in node.names)
            for name in names:
                if any(
                    name == prefix or name.startswith(prefix + ".")
                    for prefix in ("app.api", "fastapi", "starlette")
                ):
                    violations.append(f"{path.relative_to(APP)}:{node.lineno}: {name}")
    assert not violations, "\n".join(violations)


def test_routes_delegate_database_operations():
    violations = []
    operations = {
        "query",
        "execute",
        "add",
        "add_all",
        "delete",
        "commit",
        "flush",
        "merge",
    }
    for path in (APP / "api/routes").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"db", "session"}
                and node.func.attr in operations
            ):
                violations.append(f"{path.relative_to(APP)}:{node.lineno}")
    assert not violations, "\n".join(violations)


def test_routes_use_common_router():
    violations = []
    for path in (APP / "api/routes").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module == "fastapi":
                if any(alias.name == "APIRouter" for alias in node.names):
                    violations.append(str(path.relative_to(APP)))
    assert not violations, violations


def test_domain_failure_keeps_status_headers_and_permission_gate():
    app = FastAPI()
    router = Router()
    calls = []

    def permission():
        raise HTTPException(403, "Permission denied")

    def operation():
        calls.append(True)
        raise DomainError(429, "Please retry", headers={"Retry-After": "12"})

    @router.get("/limited")
    def limited():
        return operation()

    @router.get("/protected", dependencies=[Depends(permission)])
    def protected():
        return operation()

    app.include_router(router)
    with TestClient(app) as client:
        denied = client.get("/protected")
        assert denied.status_code == 403
        assert calls == []
        limited_response = client.get("/limited")
        assert limited_response.status_code == 429
        assert limited_response.json() == {"detail": "Please retry"}
        assert limited_response.headers["retry-after"] == "12"
        assert calls == [True]
