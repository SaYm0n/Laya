"""The pinned upstream Laya release this platform is built and tested against.

Laya is consumed as an external, pinned dependency; this module only records which release that is.
Changing these values is an upstream upgrade and must go through the compatibility and regression
suites (see docs/DEVELOPMENT.md, "Upstream upgrades").
"""

from typing import Final

UPSTREAM_DISTRIBUTION: Final = "laya"
UPSTREAM_VERSION: Final = "0.3.23"

# SHA-256 of the files PyPI serves for UPSTREAM_VERSION, recorded during the Phase 0 audit.
# tests/compatibility/test_upstream_pin.py checks that uv.lock pins exactly these artifacts.
UPSTREAM_WHEEL_FILENAME: Final = "laya-0.3.23-py3-none-any.whl"
UPSTREAM_WHEEL_SHA256: Final = "30247fd93dec16b131d8483b1621db198600e90c777ad7d9992e48fc118db677"
UPSTREAM_SDIST_FILENAME: Final = "laya-0.3.23.tar.gz"
UPSTREAM_SDIST_SHA256: Final = "5812dfd7bc27032a0b969b7ee2de655460e4d925ad2d55f97a869cf15ad0deba"

UPSTREAM_REPOSITORY: Final = "https://github.com/NandhaKishorM/laya"
# Commit of the v0.3.23 tag, and the main-branch commit audited in Phase 0
# (docs/UPSTREAM_ANALYSIS.md).
UPSTREAM_RELEASE_COMMIT: Final = "d8a2e59781ca135169a36095056132e273cd9938"
UPSTREAM_AUDITED_COMMIT: Final = "4aa6761be8173de4ce6d92c31b3e40b6eaf59a7c"
