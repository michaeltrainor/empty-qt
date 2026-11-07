"""Stubs for the OpenUSD Python bindings (``usd-core``), which ship none.

Each submodule stub declares a module-level ``__getattr__`` returning
``Any``, so pyright accepts ``Usd.Stage``, ``Gf.Vec3d``, and the rest
without a file-level ignore in the modules that use them. Nothing here is
imported at runtime.
"""
