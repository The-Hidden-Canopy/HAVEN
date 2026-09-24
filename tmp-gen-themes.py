"""Generate the eight flat HAVEN theme dictionaries (spec section 34-39).

One file per theme x appearance (HavenDark, HavenLight, CanopyDark, ...).
Flat token sets: ThemeService swaps one merged dictionary for another at
runtime, and Root.RequestedTheme flips framework controls.  Runs once at
authoring time; the generated XAML files are the committed artifact.
"""

import io
from pathlib import Path

OUT = Path("native/Haven.Desktop/Themes")
OUT.mkdir(parents=True, exist_ok=True)

SUCCESS, WARNING, DANGER = "#22C55E", "#F59E0B", "#EF4444"
SUCCESS_T, WARNING_T, DANGER_T = "#3322C55E", "#33F59E0B", "#33EF4444"

THEMES = {
    "Haven": {
        "dark": dict(
            canvas="#0B1220", surface1="#111A2C", surface2="#172238",
            text="#F3F6FC", muted="#8E9BB4", stroke="#27354F",
            accent="#3B82F6", accent_hover="#5B96F5", violet="#8B5CF6",
            orange="#FF8A3D",
            canvas_gradient=[("#080D18", 0), ("#0B1220", 0.55), ("#0E1A2E", 0.82), ("#14303A", 0.95), ("#1B2E4A", 1)],
            splash_gradient=[("#080D18", 0), ("#0B1220", 0.6), ("#152A44", 0.9), ("#4A2E1B", 1)],
        ),
        "light": dict(
            canvas="#F5F7FB", surface1="#FFFFFF", surface2="#F0F3F8",
            text="#172033", muted="#5C6678", stroke="#D8DFEA",
            accent="#2563EB", accent_hover="#1D4FD7", violet="#7C3AED",
            orange="#EA580C",
            canvas_gradient=[("#EDF1F8", 0), ("#F5F7FB", 0.55), ("#F8FAFD", 0.82), ("#E9F0F6", 0.95), ("#E4ECF7", 1)],
            splash_gradient=[("#EDF1F8", 0), ("#F5F7FB", 0.6), ("#E7EEF9", 0.9), ("#F3ECE6", 1)],
        ),
    },
    "Canopy": {
        "dark": dict(
            canvas="#081716", surface1="#102421", surface2="#17302C",
            text="#EFFCF8", muted="#84A89F", stroke="#294A43",
            accent="#2DD4BF", accent_hover="#5EE0D1", violet="#60A5FA",
            orange="#FB923C",
            canvas_gradient=[("#061211", 0), ("#081716", 0.55), ("#0B2320", 0.82), ("#0F2E28", 0.95), ("#14382F", 1)],
            splash_gradient=[("#061211", 0), ("#081716", 0.6), ("#0F2E28", 0.9), ("#123B32", 1)],
        ),
        "light": dict(
            canvas="#F2FAF7", surface1="#FFFFFF", surface2="#E7F3EE",
            text="#12312B", muted="#51776D", stroke="#C9E0D7",
            accent="#0D9488", accent_hover="#0F766E", violet="#3B82F6",
            orange="#EA580C",
            canvas_gradient=[("#E8F4EF", 0), ("#F2FAF7", 0.55), ("#FAFDFB", 0.82), ("#E2F0EA", 0.95), ("#DCEEE7", 1)],
            splash_gradient=[("#E8F4EF", 0), ("#F2FAF7", 0.6), ("#DDEEE7", 0.9), ("#E9F1EC", 1)],
        ),
    },
    "Ember": {
        "dark": dict(
            canvas="#121114", surface1="#1A181C", surface2="#242026",
            text="#FAF7F5", muted="#A2959C", stroke="#40353B",
            accent="#F97316", accent_hover="#FB8B3D", violet="#A78BFA",
            orange="#FB923C",
            canvas_gradient=[("#0F0E11", 0), ("#121114", 0.55), ("#1A1519", 0.82), ("#241A17", 0.95), ("#2E1D14", 1)],
            splash_gradient=[("#0F0E11", 0), ("#121114", 0.6), ("#241A17", 0.9), ("#3A2113", 1)],
        ),
        "light": dict(
            canvas="#FBF7F4", surface1="#FFFFFF", surface2="#F5EDE8",
            text="#2B211D", muted="#75655F", stroke="#E7D7CC",
            accent="#EA580C", accent_hover="#C74A0A", violet="#8B5CF6",
            orange="#EA580C",
            canvas_gradient=[("#F4ECE6", 0), ("#FBF7F4", 0.55), ("#FDFAF8", 0.82), ("#F2E7DE", 0.95), ("#F0E3D8", 1)],
            splash_gradient=[("#F4ECE6", 0), ("#FBF7F4", 0.6), ("#F0E1D5", 0.9), ("#F5E9E0", 1)],
        ),
    },
    "Mono": {
        "dark": dict(
            canvas="#111316", surface1="#191C20", surface2="#22262B",
            text="#F8FAFC", muted="#9AA1AA", stroke="#3A4048",
            accent="#E5E7EB", accent_hover="#F3F4F6", violet="#60A5FA",
            orange="#6B7280",
            canvas_gradient=[("#0E1013", 0), ("#111316", 0.55), ("#171A1E", 0.82), ("#1C2025", 0.95), ("#20252B", 1)],
            splash_gradient=[("#0E1013", 0), ("#111316", 0.6), ("#1C2025", 0.9), ("#232830", 1)],
        ),
        "light": dict(
            canvas="#F7F8FA", surface1="#FFFFFF", surface2="#EEF0F3",
            text="#17191C", muted="#5A6068", stroke="#D9DDE3",
            accent="#1F2329", accent_hover="#000000", violet="#2563EB",
            orange="#4B5563",
            canvas_gradient=[("#EFF1F4", 0), ("#F7F8FA", 0.55), ("#FCFDFE", 0.82), ("#E9ECF0", 0.95), ("#E4E8ED", 1)],
            splash_gradient=[("#EFF1F4", 0), ("#F7F8FA", 0.6), ("#E5E8EC", 0.9), ("#EBEEF2", 1)],
        ),
    },
}

HOVER = {"dark": "#22FFFFFF", "light": "#14000000"}
PERMISSION_GLOW = {"dark": "#408B5CF6", "light": "#667C3AED"}


def _tint(color: str) -> str:
    return "#33" + color.lstrip("#")


def _focus(color: str) -> str:
    return "#99" + color.lstrip("#")


def _render(palette: dict, appearance_key: str) -> str:
    hover = HOVER[appearance_key]
    focus = _focus(palette["accent"])
    glow = PERMISSION_GLOW[appearance_key]
    lines = [
        f'            <Color x:Key="HavenCanvasColor">{palette["canvas"]}</Color>',
        f'            <Color x:Key="HavenSurface1Color">{palette["surface1"]}</Color>',
        f'            <Color x:Key="HavenSurface2Color">{palette["surface2"]}</Color>',
        f'            <Color x:Key="HavenTextColor">{palette["text"]}</Color>',
        f'            <Color x:Key="HavenMutedTextColor">{palette["muted"]}</Color>',
        f'            <Color x:Key="HavenStrokeColor">{palette["stroke"]}</Color>',
        f'            <Color x:Key="HavenHoverColor">{hover}</Color>',
        "",
        f'            <Color x:Key="HavenAccentColor">{palette["accent"]}</Color>',
        f'            <Color x:Key="HavenAccentHoverColor">{palette["accent_hover"]}</Color>',
        f'            <Color x:Key="HavenVioletColor">{palette["violet"]}</Color>',
        f'            <Color x:Key="HavenOrangeColor">{palette["orange"]}</Color>',
        f'            <Color x:Key="HavenSuccessColor">{SUCCESS}</Color>',
        f'            <Color x:Key="HavenWarningColor">{WARNING}</Color>',
        f'            <Color x:Key="HavenDangerColor">{DANGER}</Color>',
        f'            <Color x:Key="HavenAccentTintColor">{_tint(palette["accent"])}</Color>',
        f'            <Color x:Key="HavenVioletTintColor">{_tint(palette["violet"])}</Color>',
        f'            <Color x:Key="HavenOrangeTintColor">{_tint(palette["orange"])}</Color>',
        f'            <Color x:Key="HavenSuccessTintColor">{SUCCESS_T}</Color>',
        f'            <Color x:Key="HavenWarningTintColor">{WARNING_T}</Color>',
        f'            <Color x:Key="HavenDangerTintColor">{DANGER_T}</Color>',
        f'            <Color x:Key="HavenFocusRingColor">{focus}</Color>',
        f'            <Color x:Key="HavenPermissionGlowColor">{glow}</Color>',
        "",
        '            <LinearGradientBrush x:Key="HavenCanvasGradient" StartPoint="0,0" EndPoint="0.15,1">',
    ]
    for color, offset in palette["canvas_gradient"]:
        lines.append(f'                <GradientStop Color="{color}" Offset="{offset}" />')
    lines += [
        "            </LinearGradientBrush>",
        '            <LinearGradientBrush x:Key="HavenSplashGradient" StartPoint="0,0" EndPoint="0,1">',
    ]
    for color, offset in palette["splash_gradient"]:
        lines.append(f'                <GradientStop Color="{color}" Offset="{offset}" />')
    lines += [
        "            </LinearGradientBrush>",
    ]
    return "\n".join(lines)


HEADER = """<ResourceDictionary
    xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">
    <!-- {name} theme, {appearance} appearance (spec 34-39).  Flat token set:
         ThemeService swaps one merged dictionary for another live, and the
         window root's RequestedTheme flips the framework controls. -->
"""

for name, appearances in THEMES.items():
    for appearance_key, appearance_label in (("dark", "Dark"), ("light", "Light")):
        body = HEADER.format(name=name, appearance=appearance_label)
        body += _render(appearances[appearance_key], appearance_key) + "\n"
        body += "</ResourceDictionary>\n"
        path = OUT / f"{name}{appearance_label}.xaml"
        io.open(path, "w", encoding="utf-8", newline="\r\n").write(body)
        print(f"wrote {path}")
