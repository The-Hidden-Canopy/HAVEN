"""Generate Android's local theme snapshot from HAVEN Desktop dictionaries."""
import argparse
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

root = Path(__file__).resolve().parents[2]
android = root / "native" / "Haven.Android"
source = (root / "native" / "Haven.Desktop" / "Services" / "ThemeService.cs").read_text(encoding="utf-8")

def choices(name):
    match = re.search(r'public static readonly string\[\] ' + name + r'\s*=\s*\{([^}]+)\}', source)
    if not match: raise ValueError("missing desktop " + name)
    return re.findall(r'"([a-z]+)"', match.group(1))

themes, modes = choices("Themes"), choices("Modes")
palettes = {}
for theme in themes:
    palettes[theme] = {}
    for appearance in ("light", "dark"):
        path = root / "native" / "Haven.Desktop" / "Themes" / (theme.title() + appearance.title() + ".xaml")
        tree = ET.parse(path)
        tokens = {}
        for node in tree.getroot():
            if node.tag.endswith("}Color"):
                name = node.attrib.get("{http://schemas.microsoft.com/winfx/2006/xaml}Key")
                if name and node.text and re.fullmatch(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", node.text.strip()):
                    tokens[name] = node.text.strip().upper()
        if not tokens: raise ValueError("missing palette: " + str(path))
        palettes[theme][appearance] = tokens

catalog = {"schema":"haven-theme-catalog-1", "themes":[{"id":x,"name":x.title()} for x in themes],
           "modes":modes, "densities":choices("Densities"), "motions":choices("Motions"), "palettes":palettes}
json_text = json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"
lines = ["package org.thehiddencanopy.haven;", "", "// Generated from HAVEN Desktop ThemeService and Themes/*.xaml; do not edit by hand.",
         "final class ThemeCatalog {", "    static final String[] IDS = {" + ", ".join(json.dumps(x) for x in themes) + "};",
         "    static final String[] NAMES = {" + ", ".join(json.dumps(x.title()) for x in themes) + "};",
         "    static final String[] MODES = {" + ", ".join(json.dumps(x) for x in modes) + "};",
         "    static boolean validTheme(String value) { for (String id : IDS) if (id.equals(value)) return true; return false; }",
         "    static String color(String theme, boolean dark, String token) {", "        String palette = theme + (dark ? \".dark\" : \".light\");", "        switch (palette) {"]
for theme in themes:
    for appearance in ("light", "dark"):
        lines += [f'            case "{theme}.{appearance}":', '                switch (token) {']
        for key,value in sorted(palettes[theme][appearance].items()): lines.append(f'                    case "{key}": return "{value}";')
        lines += ['                    default: return null;', '                }']
lines += ['            default: return null;', '        }', '    }', '}', '']
java_text="\n".join(lines)
targets = [(android / "theme-catalog.json",json_text),(android / "src" / "org" / "thehiddencanopy" / "haven" / "ThemeCatalog.java",java_text)]
parser=argparse.ArgumentParser(); parser.add_argument('--check',action='store_true'); args=parser.parse_args()
for path, text in targets:
    if args.check:
        if not path.is_file() or path.read_text(encoding='utf-8') != text: raise SystemExit(f"Theme catalog out of date: {path}")
    else: path.write_text(text,encoding='utf-8')
print('Android catalog matches Desktop: ' + ', '.join(themes) + ' × light/dark')
