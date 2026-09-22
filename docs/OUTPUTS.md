# Output contract

The JSON contract version is `1.0`. Its root fields are `version`, `model`, `source_filename`,
`pages`, `markdown`, `warnings`, and `usage`.

Each page records its source page number, rendered dimensions, status, warnings, usage, and ordered
blocks. Each block has an ID, semantic type, Markdown, a normalized `box` containing `xmin`, `ymin`,
`xmax`, and `ymax`, and an optional description or asset path. IDs are stable only within one parse
result.

Markdown is the canonical reading-order text. Visual blocks can reference crops under `assets/`.
The output labels model-written visual descriptions as generated descriptions. HTML embeds the
crops after sanitization and contains no executable scripts. The annotated PDF draws approximate
block boxes over the source-page images.

The `usage` object records input, cached-input, cache-write, output, and reasoning tokens, along
with the number of API calls and a `complete` flag. The displayed cost uses the billed token totals
and configured GPT-6 Sol rates. It is unavailable when a failed call prevents complete usage
accounting.
