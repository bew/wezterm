

## Allow different custom glyph line thickness & underline thickness

WHY: I want thicker underline, without changing custom glyph lines

TODO: Ask @wez how I should go about accessing a config from the glyphcache (or whetever is the root obj managing rendering)
(how to pass it down & where 🤔)
|
Maybe I could pass the _config_ via `GlyphCache.cached_block` 🤔
IDEA: make a `RenderConfig` which would include only my config? or both the metrics & my config?
  (and then pass _that_ down everywhere?)

% Config logic for _config_ `custom_glyph_stroke_size`:
- If _config_ is set, use _config_
- Else, use `underline_thickness`
|
FIXME: I feel like this isn't enough..
(I don't want to set `custom_glyph_stroke_size` myself when I change `underline_thickness`, I want it to still default to the `metrics.underline_height`)
👉 `metrics.underline_height` should stay unchanged in all cases, and both `custom_glyph_stroke_size` & `underline_thickness` would be _configs_ that are used when needed/relevant (without changing metrics), and both can default to `metrics.underline_height`.
.. -> Would we still need `custom_glyph_stroke_size` ? (maybe for completeness?)
.. .. We'd still need to pass down `underline_thickness` where needed, so probably need a `RenderConfig`

RenderMetrics has all the state used by the glyph cache for rendering.
A RenderMetrics obj is created at many different places from font metrics, in places where we don't have access to the config.

Somewhere I need access the config, and pass my new _config_ down: to the RenderMetrics ? or to the GlyphCache ?

-> GlyphCache is not the place to put some config, it's really only holds render caches

TBD: Is the GlyphCache being used in all places where the RenderMetrics is needed?
Do I have access to the config there?




## For a refactoring of customglyph file to have an easier story when working with unicode custom glyphs

The customglyph file actually has ALL the custom glyph logic, caching, and the definition of builtin
glyphs like the cursor (based on various shapes).

IDEA: Split the file into multiple files (in a `customglyph` module/folder):
- all glyph drawing primitives
- caching logic
- builtin glyphs (that use drawing primitives)
- unicode glyphs (that use drawing primitives)
- …?
