# Android theme catalog handoff

The checked HAVEN desktop source defines four named palette identities:
`haven` (Haven), `canopy` (Canopy), `ember` (Ember), and `mono` (Mono).
Each has light and dark XAML dictionaries: eight token sets. `system`, `light`
and `dark` are appearance modes, not palette names. Desktop also offers
`comfortable`/`compact` density and `system`/`reduced` motion; the narrow
layout applies a touch-safe density overlay. Desktop defaults to Haven/System,
Comfortable/System and stores `{theme, mode, density, motion}` in its own
`%LOCALAPPDATA%/Haven.Desktop/settings.json`.

`generate-theme-catalog.py` reads the exact desktop theme list and all 32 Color
tokens in each of the eight dictionaries, producing `theme-catalog.json` and
Android `ThemeCatalog.java`. `--check` fails if generated data diverges from
the source. The schema is `haven-theme-catalog-1`; `theme_fixture.py` supplies
a read-only fixture for a future owner-reviewed host catalog adapter. No
existing bridge, desktop service or web component was changed for themes.

Android stores palette identity as `theme_id` and mode separately as
`appearance_mode` in app-private preferences. It migrates the previous Android
mode-only integer; default palette is Haven. Unknown stored theme IDs are kept,
with Haven colors used temporarily and an explicit unavailable label, rather
than overwritten. Mode System resolves from Android's system mode. The app and
its Latch/Harbor screens use the same selected host colors. Android does not
currently sync theme choices across devices or implement desktop's density and
motion settings; the fixture reports those options without pretending they are
Android controls. Existing desktop fixed semantic status colors remain in the
catalog. Actual device color, contrast and accessibility are not verified.
