"""Mars Decision Platform.

An independent production layer built on top of Laya (https://github.com/NandhaKishorM/laya).
Not affiliated with or endorsed by Convai Innovations.
"""

from importlib.metadata import version as _distribution_version

__version__: str = _distribution_version("laya-platform")

__all__ = ["__version__"]
