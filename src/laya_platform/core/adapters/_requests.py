"""Request checks and grouping shared by the adapters that cannot route.

``laya.Router.predict_batch`` takes heterogeneous requests; an ``Agent`` (and the HTTP batch
endpoint) evaluate one question set and one control set over many states. These helpers split a
heterogeneous batch into such homogeneous groups and put the results back in input order. Nothing
here looks at, or changes, a payload.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from laya_platform.core.errors import UnsupportedControlError
from laya_platform.core.types import DecisionRequest

ROUTING_CONTROLS = ("model", "task", "lang_guess")


def refuse_routing(controls: Mapping[str, Any], *, engine: str, where: str = "") -> None:
    """Raise when routing hints reach an engine that answers with one fixed checkpoint."""
    found = sorted(key for key in ROUTING_CONTROLS if key in controls)
    if found:
        raise UnsupportedControlError(
            f"{engine} answers with one fixed checkpoint, so {where}{found} would be ignored; "
            "use UpstreamRouterEngine to route between the upstream checkpoints"
        )


def check_requests(
    requests: object,
    *,
    allowed: Collection[str],
    engine: str,
    routing: bool = False,
) -> None:
    """Refuse a malformed request or a field ``engine`` would otherwise drop silently.

    ``routing=False`` means the engine cannot route, so a routing field gets the specific error of
    :func:`refuse_routing`.
    """
    if isinstance(requests, str | bytes) or not isinstance(requests, Sequence):
        raise TypeError("requests must be a sequence of request dictionaries")
    for index, request in enumerate(requests):
        if not isinstance(request, Mapping):
            raise TypeError(f"request {index} must be a dict, got {type(request).__name__}")
        for key in ("state", "questions"):
            if key not in request:
                raise ValueError(f"request {index} is missing required key {key!r}")
        if not routing:
            refuse_routing(request, engine=engine, where=f"request {index} field(s) ")
        unknown = sorted(set(request) - {"state", "questions", *allowed})
        if unknown:
            raise UnsupportedControlError(
                f"{engine}: request {index} has unknown field(s) {unknown}"
            )


def group_requests(
    requests: Sequence[DecisionRequest], fields: Sequence[str]
) -> list[tuple[list[int], DecisionRequest]]:
    """Indices of requests sharing the same questions and the same ``fields``, first seen first.

    Questions are compared in insertion order at every level, because option order is positional
    (the upstream groups its own batches by the same rule).
    """
    groups: dict[tuple[Any, ...], list[int]] = {}
    for index, request in enumerate(requests):
        questions = json.dumps(request["questions"], ensure_ascii=False, default=str)
        key = (questions, *(_field_key(request.get(name)) for name in fields))
        groups.setdefault(key, []).append(index)
    return [(indices, requests[indices[0]]) for indices in groups.values()]


def _field_key(value: Any) -> Any:
    # Hashable stand-in for a per-request control; a callable is grouped by identity.
    return value if value is None or isinstance(value, str | int | float | bool) else id(value)
