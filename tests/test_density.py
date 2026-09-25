"""Density-token contract tests (spec section 40).

Mirrors test_themes.py's approach: no C# test runner exists, so these tests
parse the committed XAML. Density.Comfortable/Compact are flat resource
dictionaries swapped live by ThemeService.Apply, the same mechanism used for
the color theme dictionaries - see that module's docstring for why the swap
isn't WinUI's built-in ThemeDictionaries.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

THEMES_DIR = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "Themes"
APP_XAML = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "App.xaml"
THEME_SERVICE = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "Services" / "ThemeService.cs"
MAINWINDOW_ADAPTIVE = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "MainWindow.Adaptive.cs"

DENSITY_FILES = {
    "comfortable": "DensityComfortable.xaml",
    "compact": "DensityCompact.xaml",
}

# The touch-safe overlay (spec 06/07/21/40): merged on top of whichever
# density the user picked whenever the shell goes narrow (tablet width) -
# see MainWindow.Adaptive.cs's ApplyAdaptiveLayout / ThemeService.SetTouchOverlay.
TOUCH_FILE = "DensityTouch.xaml"
TOUCH_TARGET_FLOOR = 40.0  # px; the ~44px guideline with a little slack

DOUBLE_KEYS = {
    "DensityNavItemHeight",
    "DensityListItemMinHeight",
    "DensityButtonMinHeight",
    "DensityTabButtonMinHeight",
    "DensityCircleButtonSize",
}
THICKNESS_KEYS = {
    "DensityNavItemMargin",
    "DensityNavItemPadding",
    "DensityListItemMargin",
    "DensityListItemPadding",
    "DensityCardPadding",
    "DensityButtonPadding",
    "DensityPrimaryButtonPadding",
    "DensityTabButtonPadding",
    "DensityCircleButtonRadius",
}
REQUIRED_KEYS = DOUBLE_KEYS | THICKNESS_KEYS

_XAML_NS = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
_XAML_X_NS = "{http://schemas.microsoft.com/winfx/2006/xaml}"


def _thickness_max(value: str) -> float:
    parts = [float(p) for p in value.split(",")]
    return max(parts)


def _parse_density_file(path: Path) -> dict[str, str]:
    root = ET.parse(path).getroot()
    values: dict[str, str] = {}
    for child in root:
        key = child.attrib.get(f"{_XAML_X_NS}Key")
        if key is None:
            continue
        values[key] = (child.text or "").strip()
    return values


def _all_densities() -> dict[str, dict[str, str]]:
    return {name: _parse_density_file(THEMES_DIR / file) for name, file in DENSITY_FILES.items()}


def test_both_density_files_are_well_formed_xml():
    for file in DENSITY_FILES.values():
        ET.parse(THEMES_DIR / file)


def test_both_densities_define_the_full_token_set():
    for name, values in _all_densities().items():
        missing = REQUIRED_KEYS - set(values)
        assert not missing, f"{name}: missing {sorted(missing)}"


def test_compact_is_never_larger_than_comfortable():
    densities = _all_densities()
    comfortable, compact = densities["comfortable"], densities["compact"]
    for key in DOUBLE_KEYS:
        c, k = float(comfortable[key]), float(compact[key])
        assert k <= c, f"{key}: compact {k} > comfortable {c}"
    for key in THICKNESS_KEYS:
        c, k = _thickness_max(comfortable[key]), _thickness_max(compact[key])
        assert k <= c, f"{key}: compact {k} > comfortable {c}"


def test_app_xaml_styles_reference_density_tokens_not_literals():
    text = APP_XAML.read_text(encoding="utf-8")
    for key in REQUIRED_KEYS:
        assert f"{{ThemeResource {key}}}" in text, f"App.xaml: no style references {key}"
    # The styles this phase targets (nav/list/card/button/tab) must not carry
    # their old hardcoded pixel values anymore.
    for style_key, literal in (
        ("HavenNavItemStyle", 'Value="38"'),
        ("HavenListItemStyle", 'Value="40"'),
        ("HavenCardStyle", 'Value="14"'),
    ):
        block_match = re.search(
            rf'<Style x:Key="{style_key}".*?</Style>', text, re.DOTALL
        )
        assert block_match, f"App.xaml: missing {style_key}"
        assert literal not in block_match.group(0), (
            f"App.xaml: {style_key} still hardcodes {literal} instead of a density token"
        )


def test_theme_service_swaps_the_density_dictionary_live():
    text = THEME_SERVICE.read_text(encoding="utf-8")
    assert "ms-appx:///Themes/Density" in text
    assert "DefaultDensity" in text or "Densities" in text


def test_touch_overlay_is_well_formed_and_complete():
    ET.parse(THEMES_DIR / TOUCH_FILE)
    values = _parse_density_file(THEMES_DIR / TOUCH_FILE)
    missing = REQUIRED_KEYS - set(values)
    assert not missing, f"touch: missing {sorted(missing)}"


def test_touch_overlay_meets_the_touch_target_floor():
    values = _parse_density_file(THEMES_DIR / TOUCH_FILE)
    for key in DOUBLE_KEYS:
        assert float(values[key]) >= TOUCH_TARGET_FLOOR, (
            f"{key}: {values[key]} below the {TOUCH_TARGET_FLOOR}px touch-target floor"
        )


def test_touch_overlay_never_shrinks_comfortable():
    comfortable = _parse_density_file(THEMES_DIR / DENSITY_FILES["comfortable"])
    touch = _parse_density_file(THEMES_DIR / TOUCH_FILE)
    for key in DOUBLE_KEYS:
        assert float(touch[key]) >= float(comfortable[key]), (
            f"{key}: touch {touch[key]} < comfortable {comfortable[key]}"
        )
    for key in THICKNESS_KEYS:
        assert _thickness_max(touch[key]) >= _thickness_max(comfortable[key]), (
            f"{key}: touch overlay padding shrank below comfortable"
        )


def test_adaptive_layout_toggles_the_touch_overlay_on_narrow_width():
    text = MAINWINDOW_ADAPTIVE.read_text(encoding="utf-8")
    assert "SetTouchOverlay(narrow" in text
