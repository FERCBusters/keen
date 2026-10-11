"""Common HTTP router: service failures always have the same error contract."""

from fastapi import APIRouter

from app.api.errors import DomainRoute


class Router(APIRouter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("route_class", DomainRoute)
        super().__init__(*args, **kwargs)
