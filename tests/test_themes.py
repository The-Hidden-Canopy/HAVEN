"""Theme-token contract tests (spec sections 34-40).

There is no C# test runner in this repo, so these tests parse the committed
XAML: every theme ships Dark and Light appearance dictionaries with the full
semantic token set, the spec palettes are honored exactly, and text/background
pairs meet a 3:1 contrast floor.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

THEMES_DIR = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "Themes"
APP_XAML = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "App.xaml"

THEME_FILES = {
    "haven": "Haven.xaml",
    "canopy": "Canopy.xaml",
    "ember": "Ember.xaml",
    "mono": "Mono.xaml",
}

REQUIRED_COLORS = {
    "HavenCanvasColor",
    "HavenSurface1Color",
    "HavenSurface2Color",
    "HavenTextColor",
    "HavenMutedTextColor",
    "HavenStrokeColor",
    "HavenHoverColor",
    "HavenAccentColor",
    "HavenAccentHoverColor",
    "HavenVioletColor",
    "HavenOrangeColor",
    "HavenSuccessColor",
    "HavenWarningColor",
    "HavenDangerColor",
    "HavenAccentTintColor",
    "HavenVioletTintColor",
    "HavenOrangeTintColor",
    "HavenSuccessTintColor",
    "HavenWarningTintColor",
    "HavenDangerTintColor",
    "HavenFocusRingColor",
    "HavenPermissionGlowColor",
}

REQUIRED_GRADIENTS = {"HavenCanvasGradient", "HavenSplashGradient"}

# Exact spec palette spot-checks (spec pages 36-39, dark appearance).
SPEC_DARK_HEX = {
    "haven": {
        "HavenCanvasColor": "#0B1220",
        "HavenSurface1Color": "#111A2C",
        "HavenSurface2Color": "#172238",
        "HavenAccentColor": "#3B82F6",
        "HavenVioletColor": "#8B5CF6",
        "HavenOrangeColor": "#FF8A3D",
        "HavenTextColor": "#F3F6FC",
        "HavenStrokeColor": "#27354F",
    },
    "canopy": {
        "HavenCanvasColor": "#081716",
        "HavenSurface1Color": "#102421",
        "HavenSurface2Color": "#17302C",
        "HavenAccentColor": "#2DD4BF",
        "HavenVioletColor": "#60A5FA",
        "HavenTextColor": "#EFFCF8",
        "HavenStrokeColor": "#294A43",
    },
    "ember": {
        "HavenCanvasColor": "#121114",
        "HavenSurface1Color": "#1A181C",
        "HavenSurface2Color": "#242026",
        "HavenAccentColor": "#F97316",
        "HavenVioletColor": "#A78BFA",
        "HavenTextColor": "#FAF7F5",
        "HavenStrokeColor": "#40353B",
    },
    "mono": {
        "HavenCanvasColor": "#111316",
        "HavenSurface1Color": "#191C20",
        "HavenSurface2Color": "#22262B",
        "HavenAccentColor": "#E5E7EB",
        "HavenVioletColor": "#60A5FA",
        "HavenTextColor": "#F8FAFC",
        "HavenStrokeColor": "#3A4048",
    },
}

SEMANTIC_ALIASES = {
    "CanvasBaseBrush",
    "SurfacePrimaryBrush",
    "SurfaceSecondaryBrush",
    "TextPrimaryBrush",
    "TextSecondaryBrush",
    "TextMutedBrush",
    "StrokeSubtleBrush",
    "AccentBrush",
    "AccentHoverBrush",
    "FocusRingBrush",
    "PermissionGlowBrush",
    "SuccessBrush",
    "WarningBrush",
    "DangerBrush",
    "SuccessTintBrush",
    "WarningTintBrush",
    "DangerTintBrush",
    "Series1Brush",
    "Series2Brush",
    "Series3Brush",
    "Series4Brush",
    "Series5Brush",
    "Series6Brush",
    "Series7Brush",
    "Series8Brush",
    "HeatLowBrush",
    "HeatMedBrush",
    "HeatHighBrush",
}

_HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
_XAML_NS = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
_XAML_X_NS = "{http://schemas.microsoft.com/winfx/2006/xaml}"


def _parse_theme(path: Path) -> dict[str, dict[str, str]]:
    root = ET.parse(path).getroot()
    result: dict[str, dict[str, str]] = {}
    theme_dicts = root.find(f"{_XAML_NS}ResourceDictionary.ThemeDictionaries")
    assert theme_dicts is not None, f"{path.name}: missing ThemeDictionaries"
    for appearance in ("Dark", "Light"):
        node = theme_dicts.find(f"{_XAML_NS}ResourceDictionary[@{_XAML_X_NS}Key='{appearance}']")
        assert node is not None, f"{path.name}: missing {appearance} dictionary"
        colors = {}
        for child in node:
            key = child.attrib.get(f"{_XAML_X_NS}Key")
            if key is None:
                continue
            if child.tag == f"{_XAML_NS}Color":
                value = (child.text or "").strip()
                assert _HEX_RE.match(value), f"{path.name} {appearance} {key}: bad hex {value!r}"
                colors[key] = value.upper()
        result[appearance] = colors
    return result


def _srgb_channel(value: str) -> float:
    v = value / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _relative_luminance(argb: str) -> float:
    hex_part = argb.lstrip("#")
    if len(hex_part) == 8:
        hex_part = hex_part[2:]
    r, g, b = (int(hex_part[i : i + 2], 16) for i in (0, 2, 4))
    return (
        0.2126 * _srgb_channel(r)
        + 0.7152 * _srgb_channel(g)
        + 0.0722 * _srgb_channel(b)
    )


def _contrast(first: str, second: str) -> float:
    lum_a = _relative_luminance(first)
    lum_b = _relative_luminance(second)
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def _all_themes() -> dict[str, dict[str, dict[str, str]]]:
    return {name: _parse_theme(THEMES_DIR / file) for name, file in THEME_FILES.items()}


def test_every_theme_has_both_appearances_with_the_full_token_set():
    for name, appearances in _all_themes().items():
        for appearance, colors in appearances.items():
            missing = REQUIRED_COLORS - set(colors)
            assert not missing, f"{name}/{appearance}: missing {sorted(missing)}"


def test_every_theme_defines_both_canvas_gradients():
    for name, file in THEME_FILES.items():
        root = ET.parse(THEMES_DIR / file).getroot()
        theme_dicts = root.find(f"{_XAML_NS}ResourceDictionary.ThemeDictionaries")
        for appearance in ("Dark", "Light"):
            node = theme_dicts.find(f"{_XAML_NS}ResourceDictionary[@{_XAML_X_NS}Key='{appearance}']")
            gradients = {
                child.attrib.get(f"{_XAML_X_NS}Key")
                for child in node
                if child.tag == f"{_XAML_NS}LinearGradientBrush"
            }
            assert REQUIRED_GRADIENTS <= gradients, f"{name}/{appearance}: missing gradients"


def test_spec_dark_palettes_are_honored_exactly():
    themes = _all_themes()
    for name, expected in SPEC_DARK_HEX.items():
        for key, hex_value in expected.items():
            actual = themes[name]["Dark"].get(key)
            assert actual == hex_value.upper(), f"{name}/Dark {key}: {actual} != {hex_value}"


def test_text_on_canvas_and_surface_meets_contrast_floor():
    for name, appearances in _all_themes().items():
        for appearance, colors in appearances.items():
            for text_key in ("HavenTextColor", "HavenMutedTextColor"):
                for surface_key in ("HavenCanvasColor", "HavenSurface1Color", "HavenSurface2Color"):
                    ratio = _contrast(colors[text_key], colors[surface_key])
                    assert ratio >= 3.0, (
                        f"{name}/{appearance} {text_key} on {surface_key}: {ratio:.2f} < 3.0"
                    )


def test_tint_colors_carry_20_percent_alpha():
    for name, appearances in _all_themes().items():
        for appearance, colors in appearances.items():
            for base, tint in (
                ("HavenAccentColor", "HavenAccentTintColor"),
                ("HavenVioletColor", "HavenVioletTintColor"),
                ("HavenOrangeColor", "HavenOrangeTintColor"),
            ):
                expected = "#33" + colors[base].lstrip("#")
                assert colors[tint] == expected, (
                    f"{name}/{appearance} {tint}: {colors[tint]} != {expected}"
                )


def test_app_xaml_defines_every_semantic_alias():
    text = APP_XAML.read_text(encoding="utf-8")
    for alias in SEMANTIC_ALIASES:
        assert f'x:Key="{alias}"' in text, f"App.xaml: missing semantic alias {alias}"


def test_app_xaml_loads_the_default_theme_dictionary():
    text = APP_XAML.read_text(encoding="utf-8")
    assert 'Source="Themes/Haven.xaml"' in text


def test_semantic_aliases_resolve_to_theme_defined_colors():
    text = APP_XAML.read_text(encoding="utf-8")
    themes = _all_themes()
    alias_colors = dict(
        re.findall(r'x:Key="(\w+)"\s+Color="\{ThemeResource (\w+)\}"', text)
    )
    literal_colors = dict(
        re.findall(r'x:Key="(\w+)"\s+Color="(#[0-9A-Fa-f]{6,8})"', text)
    )
    for alias in SEMANTIC_ALIASES:
        assert alias in alias_colors or alias in literal_colors, (
            f"App.xaml: {alias} is neither a ThemeResource alias nor a literal color"
        )
    for name, appearances in themes.items():
        for appearance, colors in appearances.items():
            for alias, target in alias_colors.items():
                if alias in SEMANTIC_ALIASES:
                    assert target in colors, (
                        f"{name}/{appearance}: alias {alias} targets {target}, "
                        "which the theme does not define"
                    )
    for alias, value in literal_colors.items():
        if alias in SEMANTIC_ALIASES:
            assert _HEX_RE.match(value), f"App.xaml: {alias} has invalid hex {value!r}"


def test_theme_files_are_well_formed_xml():
    for file in THEMES_DIR.glob("*.xaml"):
        ET.parse(file)
