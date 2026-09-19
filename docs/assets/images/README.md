# Screenshot checklist

This folder currently holds generated placeholder PNGs (a gray box with a
label) so the docs site renders cleanly. Replace each file in place — same
filename, same aspect ratio (~1200×675) is easiest — with a real screenshot,
and no Markdown needs to change.

- [ ] `explore-ui-overview.png` — Run `vouch explore --open` in a project with a
  few recorded runs (e.g. `examples/tutorial/finished/`, after `python
  experiment.py` and `vouch build`). Capture the left-nav script/function tree
  plus the main value list, at ~1200px wide.
- [ ] `explore-ui-function-detail.png` — In the same page, click into a tracked
  function (e.g. `evaluate`). Capture the detail view with its keys and the
  "copy `\vouch{...}`" button visible.
- [ ] `terminal-vouch-check-pass.png` — Terminal screenshot of `vouch check`
  ending in `✓ OK`, with terminal colors on. (Tutorial step 5.)
- [ ] `terminal-vouch-check-fail.png` — **Highest priority.** Terminal
  screenshot of `vouch check` after breaking a claim (tutorial step 6: change
  `K = 15` to `K = 1`), showing the `FAILED: N error(s), M warning(s)` output
  with red ✗ and yellow `!` markers. This is the single most persuasive image
  on the whole site.
- [ ] `pdf-provenance-link.png` — Compiled tutorial PDF (`latexmk -pdf
  main.tex` in `examples/tutorial/finished/paper/`), a paragraph with a
  `\vouch{...}`-produced number visibly underlined/colored as a hyperlink.
- [ ] `pdf-provenance-appendix.png` — The "Value provenance" appendix page that
  link jumps to, showing the call, seeds, command and commit for one value.

Each image is referenced with standard Markdown, e.g.:

```markdown
![vouch check failing after a code change](terminal-vouch-check-fail.png)
*`vouch check` after changing `K = 15` to `K = 1`.*
```
