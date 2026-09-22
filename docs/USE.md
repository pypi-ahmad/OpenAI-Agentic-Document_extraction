# Using the app

1. Start the app with `.\launch.cmd` and open `http://127.0.0.1:9674`.
2. Confirm that you are authorized to send the document pages to OpenAI.
3. Upload one or more scanned PDFs or images and select inclusive page ranges.
4. Inspect **Input preview**, then select **Parse documents**.
5. Review rendered and raw Markdown, annotations, HTML, JSON, the displayed warning messages,
   token usage, and cost.
6. Download individual artifacts or the complete ZIP.

Submitting the same file bytes and page ranges again reuses the result held in the session. Reset
clears session results without deleting source files or shared caches.

The CLI exits with `0` for an all-`ok` result, `2` when any page is `partial`, and `1` when any page
is `failed`. It writes available artifacts before returning the status code.
