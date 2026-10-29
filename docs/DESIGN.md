---
name: Technics AZ100 Tray Flyout
description: A Windows 11 tray flyout drawn as a hearing chart. Paper, ink, hairline rules, and two reserved inks for the left and right earbud.
colors:
  paper: "#fbfbfa"
  ink: "#14181f"
  mute: "#5d6672"
  rule: "#dfe2e6"
  track: "#8d949f"
  well: "#f1f2f3"
  left-blue: "#1f5fbf"
  right-red: "#c8321f"
  paper-dark: "#15181d"
  ink-dark: "#eef0f3"
  mute-dark: "#9aa3af"
  rule-dark: "#2a2f37"
  track-dark: "#5f6874"
  well-dark: "#1d2127"
  left-blue-dark: "#6ea2ff"
  right-red-dark: "#ff7a66"
typography:
  numeral:
    fontFamily: "Segoe UI Variable Display, Segoe UI Light, Segoe UI, system-ui, sans-serif"
    fontSize: "58px"
    fontWeight: 300
    lineHeight: 0.9
    letterSpacing: "-0.04em"
    fontFeature: "tnum"
  title:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
    letterSpacing: "-0.01em"
  body:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.35
  label:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.35
  tick:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "10px"
    fontWeight: 400
    fontFeature: "tnum"
  plot-label:
    fontFamily: "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    fontFeature: "tnum"
rounded:
  inner: "6px"
  control: "8px"
  placeholder: "4px"
  thumb: "50%"
  flyout: "OS-drawn (DWM round corners)"
spacing:
  hairline: "1px"
  xs: "3px"
  sm: "6px"
  md: "12px"
  lg: "16px"
components:
  flyout-plate:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    padding: "16px 16px 12px"
    width: "344px"
    height: "478px"
  segmented-control:
    backgroundColor: "{colors.well}"
    textColor: "{colors.mute}"
    rounded: "{rounded.control}"
    padding: "3px"
  segmented-option-selected:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.inner}"
    padding: "9px 0"
  segmented-sub-option:
    textColor: "{colors.mute}"
    rounded: "{rounded.inner}"
    padding: "5px 0"
  option-band:
    height: "78px"
  auto-switch:
    textColor: "{colors.ink}"
    rounded: "{rounded.inner}"
    padding: "5px 0"
  auto-switch-disabled:
    textColor: "{colors.mute}"
  level-numeral:
    textColor: "{colors.ink}"
    typography: "{typography.numeral}"
  level-numeral-off:
    textColor: "{colors.mute}"
    typography: "{typography.numeral}"
  slider-thumb:
    backgroundColor: "{colors.paper}"
    rounded: "{rounded.thumb}"
    size: "18px"
  status-footer:
    textColor: "{colors.mute}"
    typography: "{typography.label}"
    height: "30px"
  retry-button:
    textColor: "{colors.ink}"
    rounded: "{rounded.inner}"
    padding: "4px 10px"
---

# Design System: Technics AZ100 Tray Flyout

## Overview

**Creative North Star: "The Audiogram Chart"**

The flyout is a hearing chart that happens to hold controls. Paper ground (deep ink in dark), ink text, hairline rules, and nothing that glows. The selected listening mode is a solid ink fill, the level is one large light numeral, and the battery is plotted the way an audiologist plots thresholds: left earbud a blue X, right earbud a red O, case a square, on a whisper-thin grid. It is a Windows 11 tray flyout (344 x 478 CSS px) in WebView2, not a website, and it follows the OS light or dark setting.

Density is calm and fixed. One 16px column, one plate, five stacked bands (header, mode control with its option band, level, battery, footer) separated by hairlines and 16px gaps. Every phase (loading, ready, Off, error) and every mode uses the same geometry, so nothing shifts when the state changes; the option band reserves its height whatever it holds. There are no shadows inside the flyout; depth is carried by the OS window shadow and rounded corners only.

**Key Characteristics:**
- Two inks beyond neutral, both reserved: blue means the left earbud, red means the right earbud, nowhere else.
- Hairline rules (1px) are the only structure; no cards, no fills except the segmented well, the selected option and the switch track when on.
- Segoe UI Variable with tabular figures; a single 58px light numeral is the one large moment.
- Honest state: the UI shows only what the earbuds confirmed by read-back, and an unknown optional state is disabled and muted, never a claimed value.
- One authored motion: a 170ms rise on open.

## Colors

Cool paper and blue-black ink, with a muted grey for secondary text and two clinical inks for the chart marks. Light and dark are separate literal palettes switched by `prefers-color-scheme`; the dark blue and red are lightened to keep contrast on deep ink.

### Primary
- **Blue-Black Ink** (`ink`, #14181f light / `ink-dark` #eef0f3): all primary text, the selected mode fill, the filled part of the slider, the thumb outline, the case square, the Auto switch track outline, knob and on-fill, focus rings. Inverts in dark.

### Secondary
- **Clinical Blue** (`left-blue`, #1f5fbf light / `left-blue-dark` #6ea2ff): the left earbud X mark and its "Left" label in the plot. Nothing else.
- **Clinical Red** (`right-red`, #c8321f light / `right-red-dark` #ff7a66): the right earbud O mark and its "Right" label. Nothing else, including errors.

### Neutral
- **Paper** (`paper`, #fbfbfa / `paper-dark` #15181d): window ground, thumb fill, text on the selected option, the fill inside the case and right-earbud markers, the knob when the switch is on.
- **Mute** (`mute`, #5d6672 / `mute-dark` #9aa3af): secondary text, unselected modes, helper notes, axis labels, tick marks, the Off-state and Auto-state numeral, the disabled thumb border, the disabled switch.
- **Rule** (`rule`, #dfe2e6 / `rule-dark` #2a2f37): hairline borders, the plot grid, the loading placeholder fill.
- **Track** (`track`, #8d949f / `track-dark` #5f6874): unfilled slider track, chosen to hold about 3:1 against paper.
- **Well** (`well`, #f1f2f3 / `well-dark` #1d2127): the segmented control tray (mode and sub-mode) and retry-button hover.

### Named Rules
**The Reserved Inks Rule.** Blue and red belong to the left and right earbud. They never appear as accent, state, link, or error color.
**The Ink Error Rule.** Errors speak in ink: the footer message goes from mute to ink at weight 600. Red is not an error color here.
**The Muted Ink Rule.** De-emphasis is a different ink (`mute`), never reduced opacity on text.

## Typography

**Display Font:** Segoe UI Variable Display (with Segoe UI Light, Segoe UI, system-ui)
**Body Font:** Segoe UI Variable Text (with Segoe UI, system-ui, sans-serif)

**Character:** The Windows system voice, used plainly. Weight and size do the hierarchy; the only display moment is a light-weight numeral that reads like a dial value.

### Hierarchy
- **Numeral** (300, 58px, 0.9, -0.04em, tabular): the active level, or the word "Auto" while the earbuds choose, right-aligned over the slider, min-width 2.2ch so it never shifts.
- **Title** (600, 15px, -0.01em): device name "EAH-AZ100".
- **Body** (400, 13px, 1.35): mode option labels and the "Auto level" switch label.
- **Label** (600 for section and footer actions, 400 for footer text, helper notes and level label, 12px): "Battery" heading, footer status, retry, link/codec text, level label, option-band notes, Transparent / Attention sub-options (12px).
- **Tick** (400, 10px, tabular): slider scale numbers 1, 25, 50, 75, 100.
- **Plot label** (600, 11px, tabular): "Left 80%", "Case 55%", "Right 70%" in the matching ink; axis numbers (0 and 100) at 400 in mute.

### Named Rules
**The Tabular Rule.** Every number (level, ticks, battery, codec rate) uses tabular figures so values do not jitter while changing.

## Layout

A single full-height flex column inside a 344 x 478 CSS px flyout: padding 16px (12px at the bottom), 16px between bands. Bands in order: header (title left, connection/codec text right, baseline-aligned), segmented mode control, the option band, level row plus slider plus ruled ticks, battery plot under a top hairline with 10px padding, then a footer pinned to the bottom (`margin-top: auto`) with a top hairline and a 30px minimum height.

The option band is a reserved 78px block directly under the mode control. Its content follows the mode and exactly one pane shows at a time: the Off note (with the shortcut hint), the Auto level switch with its helper line, or the Transparent / Attention sub-control with its helper text (6px between control and note). Loading, error and stale states hide all panes and keep the height. 78px fits the two-line Attention helper with slack at larger text scales.

The battery section is compact: a 12px heading plus an SVG with a 312 x 60 viewBox (about 91px with the heading). Three columns at x 79, 170, 261 (left, case, right), horizontal rules at 0/50/100 (y 38, 22, 6), y-axis labels at 0 and 100 only, column labels at y 57. Spacing steps in use: 1, 3, 6, 12, 16px. Overflow is hidden; the window never scrolls. The native shell places the flyout 12px from the taskbar edge in the work area.

## Elevation & Depth

Flat. Inside the flyout there are no shadows at all; structure comes from hairline rules and one tonal well behind the segmented controls. The only depth is the OS window shadow and rounded corners applied by DWM to the frameless window.

### Named Rules
**The No Shadow Rule.** No `box-shadow` as depth inside the flyout. (The only `box-shadow` in the stylesheet is a 2px ink focus ring on the segmented options, which is a focus indicator, not elevation.)

## Shapes

Quiet geometry with a small radius ladder: flyout corners are OS-drawn (DWM round preference), segmented tray 8px, inner options, the switch button and the retry button 6px, loading placeholders 4px, slider thumb a circle (18px). The switch track is a 36 x 18px box with 8px radius and a 2px ink outline, holding a 10px round knob. Borders are always 1px hairline in `rule`, except the thumb (2px ink), the switch track (2px ink, mute when disabled) and the retry button (1px mute). The chart marks are the signature forms: X (left, blue, 2.2px stroke, 10px), square (case, ink, 2px stroke, 10px), circle (right, red, 2.2px stroke, 5.5px radius). The case and right markers carry a paper fill so the hairline grid does not show through them.

## Components

### Segmented mode control
Three options, Off / Noise cancelling / Ambient, in columns of 1fr 1.5fr 1.2fr inside an 8px well with a 1px rule border, 3px padding and gap. Options are 13px, mute text, 6px radius, 9px vertical padding. Hover moves text to ink. Selected is solid ink fill with paper text at weight 600 (inverts in dark). Pressed scales to 0.98. Focus shows a 2px ink ring inset against a paper outline. It is a radiogroup with arrow-key navigation; roving tabindex starts on the confirmed mode. Disabled and loading options stay mute and nothing appears selected.

### Option band
A reserved 78px block under the mode control; see Layout. Notes are 12px mute.
- **Off:** one mute sentence, "Noise cancelling and ambient sound are off.", followed by the shortcut hint ("Ctrl+Alt+E cycles the mode.") when the shortcut is registered.
- **Noise cancelling:** the Auto level switch and one line of helper text.
- **Ambient:** the Transparent / Attention two-part radiogroup (same well, selected-ink and arrow-key behavior as the mode control, in a 1fr 1fr grid, 5px vertical padding, 12px text) with a helper line beneath: "Lets in all sounds around you." or the Attention text that says audio stops when switching to Ambient.

### Auto level switch
A full-width row (role=switch): 13px label "Auto level" on the left, a 36 x 18px track on the right. Off: ink outline, transparent track, ink knob at the left. On: ink-filled track, paper knob slid right. Unknown (the earbuds did not report it): the control is disabled, label, outline and knob in mute, and the knob parked mid-track so it claims neither state. 120ms color and knob transitions. Focus is the standard 2px ink outline offset 2px.

### Level slider with ruled ticks
A label (12px mute) sits left of the numeral. The slider is a 2px track, ink up to the fill and `track` after, with an 18px paper thumb and 2px ink border (scales to 1.08 on drag). Below it, five real tick marks (1px by 6px mute) with 10px numerals at 1, 25, 50, 75, 100, positioned to align with the thumb centre. Range is 1 to 100. Disabled (Off, Auto, stale, loading, error): track fully `track`, thumb border mute.
- **Off:** the saved value in mute ink with the label "Saved noise cancelling level".
- **Auto on:** numeral "Auto" in mute ink, label "Noise cancelling level, automatic", slider disabled with no thumb (transparent fill and border) and no fill on the track, because the earbuds are not using the saved value.

### Audiogram battery plot
Compact hairline grid in `rule` with rules at 0, 50 and 100; marks plotted by percentage: left blue X, case ink square, right red O; axis labels 0 and 100 in mute; each column labeled underneath in its own ink ("Left 80%"). Markers are paper-filled. Unknown values draw no mark and the label reads "--". The SVG carries a spoken label ("Battery: Left 80 percent, ..." or "unknown").

### Status footer
A top hairline, a 12px mute status line (polite live region) and, only on error, a "Try again" button (12px, weight 600, ink text, 1px mute border, 6px radius, 4px 10px, well fill on hover). Messages: Reading the earbuds, Applying, Confirmed by the earbuds, Up to date, Changed on the earbuds, The earbuds report a different setting, and the device's error text.

### Tray icon marks
Drawn at runtime (64px RGBA) as a heavy X and O side by side, in a single color: near-black ink (20,24,31) on a light taskbar, white on a dark taskbar, chosen from the system theme. Strokes are 8px so both marks hold at 16px. When not connected the same marks are drawn at reduced alpha (110/255). The icon is monochrome; it does not use the blue or red inks.

### Global shortcut and tray toast
Ctrl+Alt+E cycles Off, Noise cancelling, Ambient without opening the flyout, and a quiet toast confirms the result in plain text, composed from what is known and omitting what is not: "Off", "Noise cancelling 60", "Noise cancelling, auto", "Ambient 100", "Ambient 100, transparent", "Ambient 100, attention (pauses audio)". Failure reads "Earbuds not reachable". The tray tooltip follows the same wording ("EAH-AZ100: Noise cancelling, auto"), plus "Shortcut unavailable" on a second line when registration failed. No color or ink is involved; the toast is OS-drawn.

### States
- **Loading:** real layout with empty rule-colored placeholders for the codec text and numeral (no text to announce, `aria-busy`), controls and option band inert and empty, nothing chosen, thumb parked at 1.
- **Ready:** confirmed mode selected, numeral shows the active mode's level, link text reads "Connected, <codec> <rate> kHz", the option band shows the pane for the mode.
- **Off:** slider disabled, numeral in mute ink, label says saved level, band shows the Off note.
- **Auto:** see Level slider; the switch reads on.
- **Stale first view:** on reopening, what is drawn may predate a hotkey change, so (`body[data-stale]`) mode control, option band and level are inert for mouse and keyboard, the slider is disabled and the plate is `aria-busy`, until a fresh read lands. An epoch counter discards any read that began before a newer local change or a write start or finish.
- **Unknown optional state:** Auto level or Ambient sub-mode not reported means the control is disabled and muted, never a claimed state; a missing value on a poll means "no information", so the last known value is kept.
- **Error:** link reads "Not connected", numeral "--", controls disabled, plot emptied to "--", footer message in ink weight 600, "Try again" shown and focused.
- **Partial data:** missing battery or codec values show "--" or plain "Connected" and draw no mark; never a guess.

### Writes
Mode, level, Auto and sub-mode changes go through one serialized queue: one write in flight, at least 500ms apart, the newest choice per kind wins, and an error clears everything queued. The status line shows Applying, then Confirmed by the earbuds only after read-back.

### Motion
One authored moment: the plate rises 10px and fades in over 170ms on open (`cubic-bezier(0.16, 1, 0.3, 1)`). Small functional transitions (120ms color/background on the segmented options and switch, 120ms thumb scale and knob slide) share the same easing. All motion is removed under `prefers-reduced-motion`.

## Do's and Don'ts

### Do:
- **Do** reserve `left-blue` and `right-red` for the left and right earbud marks and labels only.
- **Do** show only read-back truth: nothing looks selected, no value shows, and no confirmation appears until the earbuds have answered.
- **Do** render an unknown optional state as disabled and muted, with the switch knob parked mid-track.
- **Do** keep controls inert (mouse and keyboard) until a fresh read lands after reopening.
- **Do** de-emphasize with `mute` ink, and let errors read in ink at weight 600.
- **Do** use tabular figures for every number and keep geometry identical across every phase and mode; new per-mode content goes inside the reserved 78px option band.
- **Do** build structure from 1px `rule` hairlines and keep corners to the 8 / 6 / 4px ladder.
- **Do** keep the focus ring a visible 2px ink outline.
- **Do** fill chart markers with `paper` so the grid does not show through.

### Don't:
- **Don't** use blue or red for accent, link, success, or error.
- **Don't** reduce text opacity to dim it, including the Off-state and Auto-state numeral and disabled options.
- **Don't** add shadows, glow, or a card stack inside the flyout.
- **Don't** let a state or mode change resize or move the layout.
- **Don't** show a thumb or fill for a level the earbuds are not using.
- **Don't** add motion beyond the 170ms rise and the short functional transitions.

## Open items and recorded drift

- **Unconfirmed mapping:** blue X = left earbud follows the official app's own mapping but has not been confirmed on hardware with unequal battery levels. Treat the plot's side assignment as provisional until it is.
- **Light mode not captured:** the light palette is specified in the stylesheet but has not been captured in the real WebView2 window.
- **Shortcut on some layouts:** Ctrl+Alt+E is AltGr+E (euro sign) on some keyboard layouts; the shortcut is configurable and the UI shows whichever label is registered.
- **Not canonized (defects the build carries):** the loading placeholders pulse their opacity (1.2s loop, to 0.55), a second motion beyond the single authored rise; the tray icon's not-connected state dims by alpha (110) rather than by a different ink.
