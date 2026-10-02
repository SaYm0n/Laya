"""Errors raised by the decision core.

Each one also derives from the builtin a caller would already catch for the same mistake, so code
written against the upstream (which raises ``TypeError``/``ValueError``/``ImportError``) keeps
working when it is given a platform engine instead.
"""

from __future__ import annotations


class EngineError(Exception):
    """Base class of every error raised by a DecisionEngine adapter itself."""


class UnsupportedOperationError(EngineError, NotImplementedError):
    """The adapter cannot offer this operation at all (e.g. ``route`` on a single checkpoint)."""


class UnsupportedControlError(EngineError, TypeError):
    """A control or request field this adapter cannot honour was passed.

    Raised instead of silently dropping it: a routing hint sent to a single checkpoint, or a
    callable ``lang_guess`` sent over HTTP, would otherwise be ignored without a trace.
    """


class MissingRuntimeError(EngineError, ImportError):
    """An optional runtime (torch, onnxruntime) needed by this adapter is not installed."""


class RemoteEngineError(EngineError):
    """A ``/v1/systemone`` endpoint answered with an error or with something that is not a payload.

    ``status`` is the HTTP status (None when no HTTP response was received) and ``detail`` the
    server's ``detail`` text when it sent one.
    """

    def __init__(self, message: str, *, status: int | None = None, detail: str | None = None):
        super().__init__(message)
        self.status = status
        self.detail = detail
