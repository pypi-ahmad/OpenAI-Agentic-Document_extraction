# Tutorial: run your first extraction

**Time:** about 10 minutes plus model processing time  
**Outcome:** Unverified draft Markdown/JSON, fail-closed v3 Markdown/JSON, a confidence report,
an annotated PDF, and an **All outputs** ZIP with `manifest.json` for one document.

## Prerequisites

- Windows 11, Python 3.13.15 (project constraint: `>=3.13.15,<3.14`), and `uv` on `PATH`
- An OpenAI API key authorized for `gpt-6-sol`
- A PDF, PNG, JPG, or JPEG that you are authorized to send to OpenAI

## 1. Prepare the project

Open PowerShell in the repository root:

```powershell
uv sync --frozen --extra gpu
```

Use `--extra cpu` instead when the declared Windows CUDA runtime is unavailable. Select one
runtime extra, not both. Reuse your configured `OPENAI_API_KEY`; if it exists at Windows User
scope but not in the current process, relaunch the terminal. Do not print the key or put it in
a tracked file. The app also supports an ignored `.streamlit/secrets.toml` credential.

## 2. Start the app

```powershell
.\launch.cmd
```

The launcher verifies whether port 9674 has a listening owner, stops only that verified process
when necessary, and starts Streamlit from the managed `uv` environment. Open
<http://127.0.0.1:9674>.

## 3. Select a document

1. In the left sidebar, select **Browse files**.
2. Choose one supported document.
3. For a PDF, set **Start page** and **End page**. Both endpoints are included.
4. Leave **Verify extraction** and **Repair flagged fields** off for an extraction-only run,
   or enable either independently. Confirm the authorization-and-review checkbox.
5. Select **Extract documents**.

The progress bar reports successful, failed, and total pages.

## 4. Inspect the result

1. Check the file status and **review required** column.
2. **Output** defaults to **Draft**. Review its unverified text against the source. Switch to
   **Verified** for the v3 artifact, where unresolved values remain redacted or null.
   **Annotated PDF** and **Usage** do not switch to a draft representation.
3. In **Usage**, inspect failures, segment scores, models, tokens, cost, and latency.
4. Treat `needs_review` segments and omitted annotations as unresolved.

## 5. Download artifacts

Download each artifact independently, or select **All outputs** for a document ZIP. The batch ZIP
includes all downloadable results and a batch manifest.

You are finished when the views are available and you have reviewed every warning. Continue with
[Use the Streamlit app](../how-to/use-the-app.md) for batch limits and failure handling.
