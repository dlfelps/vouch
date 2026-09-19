# Formatting

*Summarizes [SPEC.md §6](https://github.com/dlfelps/vouch/blob/main/SPEC.md); SPEC.md is authoritative.*

Formatting happens at **build** time from the raw recorded value, so changing a
format never requires a re-run. A value has a default `fmt` (set at record
time); a citation can override it with `\vouch[fmt]{key}`.

## Grammar

Format strings must be safe inside LaTeX source and inside `\csname`, so the
grammar is ASCII-only and uses letters for symbols that are unsafe in LaTeX
(`%` starts a comment, non-ASCII is unsafe in control-sequence names, `babel`
makes `:;!?` active in some languages):

```
fmt     := [sign] [","] ["." precision] [type] {suffix}
sign    := "+"                               always show the sign
","                                          thousands grouping, rendered {,}
type    := "f" | "e" | "g" | "d" | "pct" | "s"
suffix  := "x"      append \times           (multipliers: 1.62\times)
         | "u"      append the unit          (12.3\,ms)
         | "ci"     Stat as mean [lo, hi]    (95% CI)
         | "to"     tuple as a \to b         (56 \to 32)
```

On the Python side, `fmt=".1%"` is accepted as an alias for `.1pct`, and so is
`"{:.1%}"`. Aliases are normalized when recorded; LaTeX sources must use the
canonical form. Named formats from `[format] named` can be used anywhere a
format can: `\vouch[pct1]{key}`.

## Rendering table

| Raw value | fmt | Rendered LaTeX |
|---|---|---|
| `0.93214` | `.1pct` | `93.2\%` |
| `0.93214` | `.2f` | `0.93` |
| `18535` | `,d` | `18{,}535` |
| `1.87e-14` | `.1e` | `\ensuremath{1.9\times10^{-14}}` |
| `1.6247` | `.2fx` | `\ensuremath{1.62\times}` |
| `-0.9212` | `.2f` | `\ensuremath{-0.92}` (true minus sign) |
| `0.0213` | `+.1pct` | `\ensuremath{+2.1}\%` |
| `Stat(0.93214, 0.0041, 5)` | `.1pct` | `\ensuremath{93.2 \pm 0.4}\%` |
| `Stat(0.93214, 0.0041, 5)` | `.1pctci` | `93.2\% [92.7, 93.7]` |
| `(41.2, 53.4)` | `.0f` | `41--53` |
| `(56, 32)` | `dto` | `\ensuremath{56 \to 32}` |
| `12.34` + `unit="ms"` | `.1fu` | `12.3\,ms` (siunitx mode: `\qty{12.3}{\ms}`) |
| `"ResNet-50"` | `s` | `ResNet-50` (LaTeX-escaped) |

## Rules

- **Rounding** is half-up by default (`0.125 → 0.13`), the way readers expect, not the banker's rounding of `format()`. `half_even` is available.
- **Math.** Output containing `\times`, `\pm`, `\to`, `^`, or a leading minus sign is wrapped in `\ensuremath{}`, so `\vouch` works in text and in math.
- **Default float format.** If no format is given, `.3g` is used, and the build prints an `info` note that a float was cited without an explicit format.
- **siunitx mode** emits `\num{…}` / `\qty{…}{…}` instead of hand-built numbers, for papers that already use siunitx.
- **Pre-rendering.** Every (key, fmt) pair the paper actually uses is found by scanning and rendered in advance. LaTeX never computes anything.

See also: [LaTeX interface](latex.md).
