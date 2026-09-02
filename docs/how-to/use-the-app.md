# How to use the Streamlit app

## Process multiple documents

1. Run `.\launch.cmd` from the repository root.
2. Upload up to 20 supported files in the sidebar.
3. Choose an inclusive start and end page for each PDF.
4. Keep the batch at or below 100 selected pages and 500 MB total.
5. Confirm the authorization-and-review checkbox.
6. Select **Extract documents** once.

Each file may be at most 200 MB. Up to four documents and two pages per document are scheduled
concurrently, while a process-wide semaphore limits active OpenAI Responses calls to four. Final
pages retain deterministic source order. A separate 100,000,000-pixel raster budget protects
memory; a scan-heavy batch can exceed that budget even when it meets the file, byte, and page
limits. Reduce the selected pages or split the batch if that validation appears.

## Read progress and status

- **completed** means a page returned a usable result.
- **failed** means the page could not produce a validated result.
- **partial** means at least one page failed but successful pages were preserved.
- **review required** means a page failed or a segment remained below the quality gate.

Never interpret a partial or review-required result as complete without checking the source.

## Inspect, copy, and download output

Select a document in **View document**, then use:

- **Markdown** for rendered reading order and tables;
- **JSON** for the strict GroundTruth-compatible artifact;
- **Annotated PDF** for element boxes that could be located reliably;
- **Usage** for page/segment status, API/routing/retry counts, all token classes, latency, and cost.

The Copy control copies the corresponding existing Markdown or JSON output. It does not create a
second representation.

Document downloads are `<name>.parse.md`, `<name>.parse.json`, `<name>.annotated.pdf`, and an
**All outputs** ZIP containing those files plus `manifest.json`. The batch ZIP uses one folder per
document and adds `batch-manifest.json`. A fully failed document has no fabricated output files;
its failure is recorded in the batch manifest.

## Reset the session

Select **Reset** to clear upload widget state, page ranges, results, progress, and usage for the
current Streamlit session. Reset does not delete local files, GroundTruths, or evaluation runs.

## Inspect an existing evaluation report

Open **Evaluation comparison** in the sidebar and upload a generated `report.json`. The app
validates and summarizes that local report and offers it for download. This view never starts an
evaluation or makes an OpenAI request.

## Handle sensitive documents

- Upload only documents you are authorized to process.
- Selected page images are sent to OpenAI.
- Review artifacts before downstream use.
- Never put an API key in a document, prompt, source file, or manifest.
