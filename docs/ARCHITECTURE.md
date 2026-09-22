# Architecture

The parser uses this path:

```text
PDF or image
  -> PyMuPDF or Pillow page rendering
  -> GPT-6 Sol full-page structured read
  -> bounded full-page Markdown recovery if structured output is incomplete
  -> bounded GPT-6 Sol crop rereads when requested
  -> one document result
  -> Markdown, JSON, HTML, annotations, crops, and ZIP
```

Only GPT-6 Sol performs OCR, layout understanding, and visual interpretation. PyMuPDF and Pillow
decode, render, crop, and annotate pixels; they do not recognize document content.

For multi-page documents, each target page may receive its adjacent selected pages as continuity
context. The prompt explicitly limits transcription to the target page. Crop rereads use the same
model and merge blocks by stable within-page IDs. They are capped at four crops and two rounds.
If the structured page response is incomplete, the parser makes one plain-Markdown recovery call,
marks the page partial, and uses one full-page box instead of inventing fine-grained coordinates.

The response contract contains generic pages and semantic blocks. It has no business fields.
Normalized boxes supply the coordinates for annotations and visual crops. Every export is derived
locally from the same result, so moving between UI views cannot create more API calls.

## Prompt ownership

Every GPT-6 Sol developer prompt and dynamic user message template is a `.md` file under
`src/ade_app/prompts/`. `prompts.py` only validates filenames, loads files, selects the matching
page-context variant, and substitutes typed runtime values. No model-facing prompt prose lives in
Python.
