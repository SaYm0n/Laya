"""Data and training for specialists (Block C, F8): what to label, datasets joined to the
platform's labels, splits, an LLM teacher, and the upstream's own fine-tune recipe."""

from laya_platform.training.data import (
    SOURCES,
    SPLITS,
    DatasetBuildError,
    build_dataset,
    candidates,
    read_jsonl,
    split,
    teacher_label,
    write_jsonl,
    write_splits,
)
from laya_platform.training.finetune import (
    build_items,
    gold_for,
    run_finetune,
    specialist_manifest,
    training_rows,
    upstream_rows,
)

__all__ = [
    "SOURCES",
    "SPLITS",
    "DatasetBuildError",
    "build_dataset",
    "build_items",
    "candidates",
    "gold_for",
    "read_jsonl",
    "run_finetune",
    "specialist_manifest",
    "split",
    "teacher_label",
    "training_rows",
    "upstream_rows",
    "write_jsonl",
    "write_splits",
]
