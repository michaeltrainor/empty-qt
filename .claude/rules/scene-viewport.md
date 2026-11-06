---
paths:
  - "apps/scene_viewport/**"
---

# Scene viewport app

- `apps/scene_viewport` is an unpublished uv workspace member with its own `pyproject.toml`. Add app-only dependencies there, not to mtqt. Run it with `uv run --package scene-viewport scene-viewport`.
- pygfx ships no type annotations, so pyright infers each parameter from its default value: `intensity=1` becomes `int`, and passing a float is flagged. Silence only that, on the exact line, with `# pyright: ignore[reportArgumentType]`. Never use a file-level pyright directive or a bare ignore.
- The one other allowed ignore is `# pyright: ignore[reportAttributeAccessIssue]` on `camera.camera_matrix`: pygfx defines it with a `@cached` descriptor that pyright types for `AffineBase` only. Keep it on that line and mention it in the module comment.
- pygfx `material`, `shadow`, `geometry`, and event `target` can be `None` or another type. Narrow them with `_material_of`, `_light_shadow`, or `isinstance` in `viewport.py`.
- Don't set private attributes on pygfx objects. Keep app state in the viewport, for example in a `weakref.WeakKeyDictionary` keyed by the pygfx object.
- Tests can't open a GPU window. Test the logic directly, calling methods with a `types.SimpleNamespace` stand-in for `self` where needed, and say in the PR that the window wasn't opened.
