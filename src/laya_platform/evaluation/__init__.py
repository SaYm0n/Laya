"""Evaluation and calibration (Block A, F4): run an engine over a labelled dataset of one
DecisionSpec, report metrics with an identity, choose bands by cost, fit temperatures."""

from laya_platform.evaluation.bands import BandChoice, choose_threshold, policy_for
from laya_platform.evaluation.report import build_report, to_markdown
from laya_platform.evaluation.run import Case, DatasetError, Example, evaluate, load_examples

__all__ = [
    "BandChoice",
    "Case",
    "DatasetError",
    "Example",
    "build_report",
    "choose_threshold",
    "evaluate",
    "load_examples",
    "policy_for",
    "to_markdown",
]
