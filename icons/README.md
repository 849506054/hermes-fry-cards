# Card header icons

These are the status icons used in the screenshots. They come from
[Tabler Icons](https://github.com/tabler/tabler-icons) 3.44.0 (MIT licence),
recoloured and exported as 64x64 PNG.

| File | Use | Tabler icon | Colour |
|---|---|---|---|
| `processing.png` | running | `loader-2` (outline) | `#5E97F6` |
| `completed.png` | finished | `circle-check` (outline) | `#36A269` |
| `stopped.png` | stopped | `player-stop` (filled) | `#E06060` |
| `error.png` | error | `alert-circle` (outline) | `#E06060` |
| `cron.png` | scheduled notice | `clock` (outline) | `#F5B83D` |

Upload each PNG to Feishu, then put the returned `img_key` into the matching
`streaming.header.icon_*` config key. The card uses them as `custom_icon`, so the
title text no longer carries an emoji prefix.

`processing.svg` is the source for the running icon; the other SVGs are the
unmodified Tabler files at the URLs below.

- outline: https://cdn.jsdelivr.net/npm/@tabler/icons@3.44.0/icons/outline/
- filled: https://cdn.jsdelivr.net/npm/@tabler/icons@3.44.0/icons/filled/

Licence: MIT, Copyright (c) 2020-2026 Paweł Kuna.
