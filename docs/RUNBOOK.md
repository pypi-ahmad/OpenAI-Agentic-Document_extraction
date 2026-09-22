# Operations runbook

Use this runbook to start, stop, diagnose, and log ADE and its command-line tools.

## Start and stop procedures

### Streamlit web interface

The web interface binds to loopback interface `127.0.0.1` on port `9674` with XSRF protection enabled.

#### Starting on Windows

Run the launcher batch script from the project root:

```powershell
.\launch.cmd
```

The script invokes `scripts/launch.ps1`, which checks if port 9674 is in use. If an existing process listening on that port matches the project virtual environment Python and `streamlit_app.py`, it terminates that process, waits for the port to clear, and starts Streamlit.

#### Starting manually or on non-Windows hosts

Run Streamlit directly through `uv`:

```bash
uv run --no-sync streamlit run streamlit_app.py --server.port 9674 --server.address 127.0.0.1 --server.enableXsrfProtection true
```

#### Stopping the web interface

- Press `Ctrl+C` in the terminal hosting the running process.
- Alternatively, running `.\launch.cmd` again will automatically stop any verified existing instance before starting a new one.

### Command-line extraction

To process a single document from the terminal:

```powershell
uv run --no-sync ade-extract .\document.pdf --output-dir .\document.outputs
```

To stop a running command, press `Ctrl+C`.

## Common failures and diagnostics

Use these application error messages to diagnose common problems.

### Credential and environment failures

- **`ERROR: uv is not available on PATH`**:
  `uv` is not installed or the directory containing the binary is not in the system `PATH` environment variable. Verify installation with `uv --version`.
- **`Required credential OPENAI_API_KEY is unavailable`**:
  The environment variable `OPENAI_API_KEY` is not set and no key exists in `.streamlit/secrets.toml`. Set the variable in the active terminal session or add it to the ignored secrets file.
- **`OPENAI_API_KEY contains invalid whitespace or control characters`**:
  The API key string contains leading/trailing spaces, newline characters, or ASCII control characters. Strip surrounding whitespace.
- **`OPENAI_BASE_URL must use an official HTTPS OpenAI API hostname`**:
  The configured `OPENAI_BASE_URL` does not use the HTTPS scheme or does not target an official OpenAI domain (`api.openai.com` or `*.api.openai.com`).

### Port and startup conflicts

- **`Port 9674 is owned by unrelated PID <PID>; it was not terminated`**:
  Port 9674 is already bound by another process whose command line does not match the project virtual environment and `streamlit_app.py`. Identify the occupying process:
  ```powershell
  Get-NetTCPConnection -State Listen -LocalPort 9674 | Select-Object LocalAddress, LocalPort, OwningProcess
  ```
- **`Port 9674 did not become available after stopping its verified owner`**:
  The previous process took longer than the 5-second deadline to release the socket. Check process termination in Task Manager or PowerShell.

### Layout and accelerator errors

- **`dependency_unavailable` / `model_unavailable` / `model_init_failed`**:
  The PP-StructureV3 layout analysis package or weights could not be loaded. Ensure `paddleocr` and `paddlex` dependencies are installed and accessible.
- **`accelerator_failed`**:
  CUDA execution failed. If `allow_cpu_fallback` is enabled in configuration (default: `true`), the layout engine automatically switches to CPU and sets the latch `_CPU_LATCHED = True` for the lifetime of the process.

### API and transport errors

- **`OpenAI transport failed after <N> attempts`**:
  The client exhausted configured network retries (`transport_max_attempts`, default 3). Check outbound internet connectivity, corporate proxy settings, and OpenAI platform service availability.

### Input validation and limits

- **`batch size exceeds maximum` / `upload rejected`**:
  The uploaded batch exceeds system limits:
  - Maximum 20 files per batch
  - Maximum 200 MB per individual file
  - Maximum 500 MB total batch size
  - Maximum 100 selected pages per batch
  - Maximum 100,000,000 pixels total raster budget

## Logging location and format

- **Standard streams**: By design, the application does not write log files to the filesystem to prevent accidental logging of sensitive document data or credentials. All logs are emitted to standard output (`stdout`) and standard error (`stderr`).
- **CLI configuration**: CLI commands support `--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`) and `--log-format` (`text`, `json`).
- **Streamlit session**: Operational statuses, token usage, estimated costs, and review flags are displayed in the application UI under the **Usage** tab and packaged inside the output `manifest.json`.
