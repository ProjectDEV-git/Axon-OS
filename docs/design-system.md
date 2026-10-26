# Axon design system

How Axon looks and moves, where each value lives, and how to change it.
The rules come from three published design skills, read in full when this
was built: Emil Kowalski's `emil-design-eng` and `apple-design`
(github.com/emilkowalski/skills) and Leon Lin's `taste-skill`, app-screen
rules (github.com/Leonxlnx/taste-skill). Where they disagree, the choice is
written down below.

## One source, generated everywhere

| What | Source | Generator | Outputs |
|---|---|---|---|
| Colours, type, radii, motion, accents | `theme/tokens/axon-tokens.json` | `scripts/gen_tokens.py` | GTK tokens and accent overrides in `apps/shared/`, Shell stylesheets and accent overrides in `shell/axon-shell/`, the system GTK theme and the libadwaita accent in `theme/axon-gtk/` |
| App icons | `scripts/gen_icons.py` | same | `theme/axon-icons/scalable/apps`, `theme/axon-icons/symbolic/apps` |
| The mark (raster) | `theme/axon_mark.py` | Plymouth, GRUB and installer generators | `plymouth/axon-splash/`, `theme/grub/axon/`, `installer/branding/axon/` |
| Wallpapers | `theme/wallpapers/generate_neural.py` | same | `axon-neural-dark.png`, `axon-neural-light.png` |

Run `python3 scripts/gen_tokens.py` and `python3 scripts/gen_icons.py` after
any change; the tests fail while an output is stale.

## Colour

- Greys are true neutrals (no navy, slate or violet tint); a test checks it.
- One accent, blue by default (`#2b6fde` dark, `#1d64d8` light). It marks focus,
  selection, the one main action and AI details, nothing else. The user can
  pick teal, green, orange, pink or graphite in Axon Preferences > Appearance.
- Every text colour and every accent passes WCAG AA (4.5:1) on every surface,
  7:1 in high contrast; tests check all of them.
- Status uses colour plus an icon or a word, never colour alone.
- Space colours are identity, not text: the OSD shows them as a swatch.

## Type

Inter, in pt so Large Text works. Floor 11 px, app titles at most 32 px.
Tracking is size-specific: tighter on large text (-0.6 px at display size),
0 on body, slightly looser on small labels. Sentence case everywhere; no
tracked uppercase labels.

## Surfaces

- App windows are opaque and flat: a tone step or a faint 1px line between
  surfaces, shadows only on what floats (menus, popovers, tooltips, the
  taskbar).
- The Shell taskbar floats 8px inside the screen edges on a translucent
  material with a light top edge. Reduce Transparency (Axon Preferences) or
  High Contrast makes it solid. There is no blur: `Shell.BlurEffect` can't
  be clipped to rounded corners.
- Radii: 6 controls, 8 cards, 14 floating panels, 20 the largest surfaces.

## Motion

From `MOTION` in `shell/axon-shell/ui-logic.js` and `duration`/`easing` in the
tokens:

- Surfaces opened from the keyboard many times a day (Intent Bar, Space
  switcher, Space OSD, the Start Menu from Super) fade in 50 ms. Emil's rule
  is no animation at all; the owner asked for a 50 ms fade so they don't
  appear harshly.
- Mouse-opened menus grow from 0.97 out of the button that opened them in
  160 ms and leave the same way in 110 ms. Nothing appears from scale 0.
- Strong ease-out curves; never ease-in; no bounce (bounce is only for a
  flick, and Axon has none). Tests ban ease-in and bounce in the Shell.
- Pressed controls scale to 0.95 to 0.97 on press, not on release.
- Interruptible: reopening the Start Menu while it closes reverses it.
- Every animation honours Reduced Motion (`enable-animations`).

## Icons

Original artwork (no redrawn Apple icons): one 128-unit grid, one
superellipse tile at a shared keyline, one 7-unit stroke, one symbol each,
plus filled 16 px symbolic versions GTK recolours. Emoji are never used as
icons; a test checks the UI strings.

## Copy

- Say what a thing is. "Recent files", not "Recommended files".
- Never promise AI is local-only: cloud models are supported. Only the
  origin indicator says "Local", and only after checking the selected model.
  A test rejects phrases like "Zero Cloud" or "Entirely local".

## Where the skills disagree

| Question | Apple | Taste | Axon |
|---|---|---|---|
| Glass | Translucent chrome shows hierarchy | No glass on app screens | Translucent Shell taskbar only, solid on request; apps opaque |
| Accent hue | Any | No violet or indigo | Calm blue default (Axon used violet before) |
| Keyboard surfaces | Respond instantly | — | 50 ms fade (owner's choice over Emil's "none") |
| Gradients | Fine in icons | Banned on UI | A subtle tone step in app icons only; flat UI |
