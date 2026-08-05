"""Price and news providers.

Each module here implements the :class:`~dashboard.sources.base.Source` protocol and
is responsible for exactly one provider. Adding an instrument to an existing provider
is an `instruments.toml` edit; a new module is only needed for a new provider.
"""
