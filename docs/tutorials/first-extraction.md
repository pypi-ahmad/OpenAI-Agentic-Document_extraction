# Tutorial: run your first extraction

**Time:** about 10 minutes plus model processing time  
**Outcome:** Markdown, strict JSON, an annotated PDF, and an **All outputs** ZIP containing those
artifacts and `manifest.json` for one document.

## Prerequisites

- Windows 11, Python 3.14+, and `uv` on `PATH`
- An OpenAI API key authorized for `gpt-5.6-terra` and `gpt-5.6-sol`
- A PDF, PNG, JPG, or JPEG that you are authorized to send to OpenAI

## 1. Prepare the project

Open PowerShell in the repository root:

```powershell
uv sync --frozen
$env:OPENAI_API_KEY = "your-key-for-this-shell"
```

Do not put a real key in a tracked file or shared terminal transcript.

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
4. Confirm **I am authorized to send these pages to OpenAI and will review flagged output**.
5. Select **Extract documents**.

The progress bar reports successful, failed, and total pages.

## 4. Inspect the result

1. Check the file status and **review required** column.
2. Compare the **Markdown**, **JSON**, and **Annotated PDF** tabs.
3. In **Usage**, inspect failures, segment scores, models, tokens, cost, and latency.
4. Treat `needs_review` segments and omitted annotations as unresolved.

## 5. Download artifacts

Download each artifact independently, or select **All outputs** for a document ZIP. The batch ZIP
includes all downloadable results and a batch manifest.

You are finished when the views are available and you have reviewed every warning. Continue with
[Use the Streamlit app](../how-to/use-the-app.md) for batch limits and failure handling.
