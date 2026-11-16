"""Toolbar icons render from the packaged SVG files."""

from __future__ import annotations

import pytest

from scene_viewport.icons import ICON_NAMES, tool_icon


@pytest.mark.parametrize("name", ICON_NAMES)
def test_tool_icon_renders_visible_pixels(qtbot, name) -> None:
    pixmap = tool_icon(name).pixmap(24, 24)
    assert not pixmap.isNull()
    image = pixmap.toImage()
    opaque = sum(
        image.pixelColor(x, y).alpha() > 0
        for x in range(image.width())
        for y in range(image.height())
    )
    assert opaque > 40
