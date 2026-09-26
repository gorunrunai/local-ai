# GoRunRun Local AI — icon & logo assets

Mark: a neural network inside a chat bubble on a laptop screen. The idea is that the AI runs inside the chat, and the chat runs on your own computer.

## Colors
| Token | Hex | Use |
|---|---|---|
| ink | `#17171A` | Mark, wordmark, and app-icon background |
| paper | `#F4F1EA` | Light background, and the mark on dark |
| accent | `#E2552D` | The output node, and only that |
| muted | `#4A4740` / `#C9C3B6` | "LOCAL AI" tag on light / on dark |

## Fonts
The wordmark is Bricolage Grotesque ExtraBold (800) with -0.04em tracking. The "LOCAL AI" tag is JetBrains Mono Medium (500) with 0.28em tracking. Both are SIL OFL. In every SVG the text is converted to outlines, so no font install is needed.

## Files
- `app/icon.icns`: the macOS app icon (used by `desktop/build.sh`).
- `svg/lockup-horizontal.svg` and `svg/lockup-horizontal-on-dark.svg`: the mark plus wordmark, for light and dark backgrounds (used in the README).
- The web app's icons and marks are in `frontend/public/` (favicon, PWA icons) and `frontend/public/brand/` (the header lockup and the home-screen mark).

## Usage rules
- Use orange only on the output node. Don't recolor the rest of the mark.
- Choose the detail level by rendered size: full from 128 px up, small at 48–64 px, and favicon at 32 px and below.
- Keep clear space of at least the bubble's height around the mark.
- Don't rotate, stretch, outline, or add effects to the mark.
