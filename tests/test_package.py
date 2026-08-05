"""Scaffold smoke tests.

These exist so the toolchain has something real to run before the first source
module lands: they prove the package is importable from an editable install and
that the version hatchling reads out of ``__init__.py`` is the version installed.
"""

from importlib import metadata

import dashboard


def test_package_imports() -> None:
    assert dashboard.__version__


def test_installed_version_matches_package() -> None:
    assert metadata.version("commodities-dashboard") == dashboard.__version__
