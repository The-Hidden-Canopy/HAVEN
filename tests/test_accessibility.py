"""Accessibility contract tests (Native Product Pass phase 7).

No C# test runner and no pixel-verification available here (WinUI
compositing on the dev machine is intermittent), so this - like
test_themes.py/test_density.py - is a source-level contract: every icon-only
control that collapses to icon-only at narrow/tablet width (the nav rail,
spec 06/07/21) or never carries a visible label (the composer's circular
mic/send buttons) must expose an explicit AutomationProperties.Name, since a
screen reader's name computation for a Button falls back to visible Content
text - which the narrow-width adaptive layout collapses (see
MainWindow.Adaptive.cs's ApplyAdaptiveLayout) - and must not go silently
nameless.
"""

from __future__ import annotations

import re
from pathlib import Path

MAINWINDOW_XAML = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "MainWindow.xaml"
APP_XAML = Path(__file__).resolve().parent.parent / "native" / "Haven.Desktop" / "App.xaml"

# tag -> expected accessible name (the same text the visible label carries).
NAV_BUTTONS = {
    "today": "Today",
    "search": "Search",
    "projects": "Projects",
    "tasks": "Tasks",
    "people": "People",
    "memory": "Memory",
    "computer": "Computer",
    "communications": "Communications",
    "home": "Home",
    "models": "Models",
    "settings": "Settings",
}


def _button_start_tags(text: str) -> list[str]:
    """Every <Button ...> opening tag, attributes included, as one string each."""
    return re.findall(r"<Button\b.*?(?:/>|>)", text, re.DOTALL)


def test_every_nav_rail_button_has_an_automation_name():
    text = MAINWINDOW_XAML.read_text(encoding="utf-8")
    for tag, expected_name in NAV_BUTTONS.items():
        match = re.search(rf'<Button\b[^>]*?Tag="{tag}"[^>]*?(?:/>|>)', text, re.DOTALL)
        assert match, f"no <Button Tag=\"{tag}\"> found"
        start_tag = match.group(0)
        name_match = re.search(r'AutomationProperties\.Name="([^"]*)"', start_tag)
        assert name_match, f"nav button {tag!r}: missing AutomationProperties.Name (collapses to icon-only when narrow)"
        assert name_match.group(1) == expected_name, (
            f"nav button {tag!r}: AutomationProperties.Name {name_match.group(1)!r} != visible label {expected_name!r}"
        )


def test_composer_circle_buttons_have_automation_names():
    text = MAINWINDOW_XAML.read_text(encoding="utf-8")
    for name in ("ComposerMicButton", "ComposerSendButton"):
        match = re.search(rf'<Button\b[^>]*?x:Name="{name}"[^>]*?(?:/>|>)', text, re.DOTALL)
        assert match, f"no <Button x:Name=\"{name}\"> found"
        assert "AutomationProperties.Name=" in match.group(0), (
            f"{name}: icon-only button (no visible label) missing AutomationProperties.Name"
        )


def test_every_nav_button_start_tag_is_well_formed():
    # Sanity check on the regex approach itself: every <Button Tag="..."> we
    # rely on above must actually appear among all <Button> start tags.
    text = MAINWINDOW_XAML.read_text(encoding="utf-8")
    start_tags = _button_start_tags(text)
    tagged = [t for t in start_tags if 'Tag="' in t]
    found_tags = {re.search(r'Tag="([^"]*)"', t).group(1) for t in tagged}
    missing = set(NAV_BUTTONS) - found_tags
    assert not missing, f"nav tags not found among <Button> elements: {missing}"


def test_app_xaml_overrides_the_focus_visual_brushes():
    text = APP_XAML.read_text(encoding="utf-8")
    for key in (
        "SystemControlFocusVisualPrimaryBrush",
        "SystemControlFocusVisualSecondaryBrush",
        "FocusVisualPrimaryBrush",
        "FocusVisualSecondaryBrush",
    ):
        assert f'x:Key="{key}"' in text, f"App.xaml: missing focus-visual override {key}"
        # Every override must track the theme, not a fixed color, so it swaps
        # live with the rest of the palette.
        block = re.search(rf'<SolidColorBrush x:Key="{key}"[^/]*/>', text)
        assert block and "ThemeResource" in block.group(0), (
            f"App.xaml: {key} must bind via ThemeResource, not a literal color"
        )
