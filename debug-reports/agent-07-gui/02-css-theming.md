# Sub-Task 2: CSS Styling Consistency & Theming

**Agent 7 — GUI Applications Deep Debug**
**Date: 2026-07-02**

---

## 1. Critical: No Light Theme Support Anywhere

Every single application uses hardcoded dark-theme-only colors. There are no CSS variables, no `@define-color` named colors, and no `prefers-color-scheme` media queries. The system's light/dark theme toggle has zero effect on any Axon OS app.

**Ironically**, `axon-settings/main.py` has a "Toggle Theme" quick-action button that calls `_run_command("toggle dark mode")`, but the settings app's own CSS has no light-mode variant:
```css
window {
    background-color: #0f0f14;
    color: #e4e4e8;
}
```

**Impact:** Users who switch to a light GNOME theme will see all Axon apps remain dark, creating a jarring experience.

**Fix:** Define CSS custom properties (or GNOME named colors) at `:root` and use them throughout. Alternatively, use Adw.StyleManager to detect theme and load appropriate CSS.

**Severity:** Critical

---

## 2. High: Inconsistent Background Colors Across Apps

| App | Background Color(s) |
|-----|---------------------|
| Axon Files | `#09090f` (window), `#11111a` (file-row), `#0c0c14` (main-content) |
| Axon Settings | `#0f0f14` (window), `#0c0c10` (header) |
| Axon Terminal | `#0f0f14` (window), `#0a0a12` (header), `#12121c` (tab) |
| Axon Shortcuts | `#0f0f14` (window) |
| Intent Bar | `#09090f` (window) |
| Axon Welcome | `#09090f` (window) |
| AI Panel | `#0d0d1e` (window gradient start) |
| Sandbox Dialog | `#0b0b12` (window) |

**Bug:** At least 5 different "near-black" background colors are used (`#09090f`, `#0a0a12`, `#0b0b12`, `#0c0c10`, `#0f0f14`, `#0d0d1e`). These are perceptually similar but visually inconsistent when apps are placed side by side.

**Recommendation:** Define a single `--bg-primary` variable. Use `#0f0f14` or `#09090f` consistently.

**Severity:** High

---

## 3. High: Inconsistent Text Colors Across Apps

| App | Primary Text Color |
|-----|-------------------|
| Axon Files | `#e8e8f4` |
| Axon Settings | `#e4e4e8` |
| Axon Terminal | `#e4e4e8` |
| Axon Shortcuts | `#e2e8f0` |
| Intent Bar | `#e8e8f4` |
| Axon Welcome | `#e8e8f4` |
| AI Panel | `#e8e8f4` |
| Sandbox Dialog | `#e4e4e8` (body), `#e5e7eb` (buttons) |

**Bug:** Three different primary text colors are used: `#e8e8f4`, `#e4e4e8`, and `#e2e8f0`. While subtle, placing the Settings app next to the Files app reveals a difference in text whiteness.

**Severity:** High

---

## 4. High: Inconsistent Secondary/Muted Text Colors

| App | Muted Text Color(s) |
|-----|---------------------|
| Files | `#c0c0d8`, `#80809b`, `#9090a8` |
| Settings | `#b4b4c6` |
| Terminal | `#8888a0`, `#d0d0dc`, `#5a5a78` |
| Shortcuts | `rgba(148, 163, 184, 0.7)`, `rgba(203, 213, 225, 0.85)` |
| Intent Bar | `#9090b8`, `#70709a`, `#50507a` |
| Welcome | `#9090b8`, `#50507a` |
| AI Panel | `#50507a` |
| Sandbox | `#e4e4e8` (all same) |

**Bug:** At least 8 different muted/secondary text colors. No consistent "secondary text" or "tertiary text" variable.

**Severity:** High

---

## 5. Medium: Accent Color Inconsistency

| App | Primary Accent | Hover/Active |
|-----|---------------|-------------|
| Files | `#8b5cf6` | `#c084fc` (badge) |
| Settings | `#8b5cf6` | `#7c3aed` → `#6d28d9` |
| Terminal | `#c4b5fd` | gradient `#c4b5fd → #a78bfa` |
| Shortcuts | `#8B5CF6` / `rgba(124, 58, 237, 0.x)` | — |
| Intent Bar | `#8b5cf6` | `#ffffff` (chip hover) |
| Welcome | `#8b5cf6` | `#7c3aed` |
| AI Panel | `#8b5cf6` / gradient `#9d6ff8 → #8b5cf6` | — |
| Sandbox | `#5b21b6` (btn-sandbox) | — |

**Bug:** The terminal uses `#c4b5fd` (lighter purple) as its primary accent while all other apps use `#8b5cf6`. The sandbox uses `#5b21b6` (darker purple) for its primary action button. Three different purple shades across the ecosystem.

**Recommendation:** Standardize on `#8b5cf6` for primary accent, `#7c3aed` for hover, `#6d28d9` for active/pressed.

**Severity:** Medium

---

## 6. Medium: Inconsistent Font Stacks

| App | Font Stack |
|-----|-----------|
| Settings | `"Inter", "Outfit", "Ubuntu", sans-serif` |
| Settings feedback | `"Inter", "Ubuntu", sans-serif` |
| Shortcuts | `"Inter", "Cantarell", sans-serif` |
| Shortcuts key badge | `"JetBrains Mono", "Fira Code", "Cascadia Code", monospace` |
| Terminal NL preview | `monospace` |
| Sandbox | `"Inter", sans-serif` |
| Files | (no font specification — inherits defaults) |
| Welcome | (no font specification — inherits defaults) |
| AI Panel | (no font specification — inherits defaults) |

**Bug:** Font stacks vary significantly. "Inter" appears in 4 different fallback configurations. Files, Welcome, and AI Panel don't specify fonts at all, inheriting the system default (likely "Cantarell" on GNOME).

**Recommendation:** Define a single `--font-primary` and `--font-mono` variable. Use `"Inter", sans-serif` consistently as the primary font.

**Severity:** Medium

---

## 7. Medium: CSS Specificity Conflict — `.ai-toggle-btn`

The class `.ai-toggle-btn` is defined in both:
- `axon-files/main.css` (lines 170-179): Simple styling with border-radius, font properties, and a `:checked` state
- `axon-terminal/main.css` (lines 16-28): Full gradient background, border, padding, hover state

**If both CSS files are loaded** (e.g., when both Files and Terminal are running), the terminal's version wins because it's loaded later and has more specific rules. The Files app's AI toggle button will inherit the terminal's gradient styling instead of its own simpler styling.

**Fix:** Namespace the class names per app (e.g., `.files-ai-toggle-btn`, `.terminal-ai-toggle-btn`) or use different class names.

**Severity:** Medium

---

## 8. Medium: Unsupported CSS Properties in GTK4

### `transform` property (axon-files/main.css:80)
```css
.file-row:hover {
    transform: translateY(-1px);
}
```
**Bug:** GTK4 CSS does NOT support the standard CSS `transform` property. This rule will be silently ignored. The hover effect the developer intended (subtle lift) will not work.

**Fix:** Remove the transform rule or use a different visual indicator for hover.

### `transition` property (multiple files)
```css
.file-row { transition: all 0.15s ease; }
.feedback-card { transition: all 250ms ease; }
.shortcut-card { transition: border-color 200ms ease; }
```
**Status:** GTK4 does support a limited subset of CSS transitions, but only for `background-color`, `color`, `opacity`, and `border-color`. The `transition: all` shorthand may include unsupported properties. The `transition` on `.file-row` includes `transform` which is unsupported.

**Severity:** Medium (won't cause errors, but some transitions won't work)

### `letter-spacing` (axon-shortcuts/main.css:24)
```css
.overlay-title { letter-spacing: 0.5px; }
```
**Status:** GTK4 does not support `letter-spacing` in CSS. This will be silently ignored.

**Severity:** Low

---

## 9. Low: `!important` Usage

**No `!important` declarations found in any CSS file.** This is good practice.

---

## 10. Low: Inconsistent Border Radius Patterns

| Element | Border Radius |
|---------|---------------|
| Buttons (files, settings) | `8px` |
| Buttons (intent bar chips) | `9999px` (pill) |
| File rows (files) | `10px` |
| Shortcut cards | `12px` |
| Settings feedback card | `12px` |
| Window corners (intent bar) | `20px` |
| Key badges (shortcuts) | `6px` |
| Terminal tabs | `6px 6px 0 0` |

**Observation:** Border radii are not standardized. While some variation is intentional (cards vs buttons vs badges), the general lack of a defined scale makes the UI feel inconsistent.

**Recommendation:** Define a token scale: `--radius-sm: 6px`, `--radius-md: 8px`, `--radius-lg: 12px`, `--radius-pill: 9999px`.

**Severity:** Low

---

## 11. Low: Shadow Inconsistency

- Files: No box-shadows on windows/cards
- Settings: `box-shadow: 0 4px 16px rgba(0, 0, 0, 0.3)` on feedback card
- Intent Bar: `box-shadow: 0 8px 40px rgba(0, 0, 0, 0.8)` on window
- AI Panel: Multi-layer shadow with inset highlight
- Welcome: No shadows
- Shortcuts: No shadows

**Observation:** Shadow usage is inconsistent. Some apps use shadows for depth, others don't.

**Severity:** Low

---

## Summary Table

| Issue | Severity | Impact |
|-------|----------|--------|
| No light theme support | Critical | System theme toggle is broken |
| Inconsistent background colors | High | Visual jarring between apps |
| Inconsistent text colors | High | Subtle readability differences |
| Inconsistent muted text colors | High | 8+ different secondary colors |
| Accent color inconsistency | Medium | Terminal uses different purple |
| Font stack inconsistency | Medium | Mixed font rendering |
| CSS specificity conflict (.ai-toggle-btn) | Medium | Cross-app styling bleed |
| Unsupported CSS properties (transform) | Medium | Hover lift effect doesn't work |
| Border radius not standardized | Low | Minor visual inconsistency |
| Shadow usage inconsistent | Low | Minor depth inconsistency |
