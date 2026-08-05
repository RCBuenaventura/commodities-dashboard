"""A static, daily-updating dashboard of energy and metals prices.

The package is deliberately small: :mod:`dashboard.fetch` pulls today's prices and
appends them to the JSON history in ``data/``, and :mod:`dashboard.render` turns that
history into a static site. There is no server and no database.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
