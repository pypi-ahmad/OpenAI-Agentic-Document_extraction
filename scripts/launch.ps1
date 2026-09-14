$ErrorActionPreference = "Stop"
$port = 9674
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$venvRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot ".venv"))
$venvPython = [IO.Path]::GetFullPath((Join-Path $venvRoot "Scripts\python.exe"))
$appName = "streamlit_app.py"

try {
    $owners = @(
        Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    foreach ($processId in $owners) {
        $stillOwnsPort = Get-NetTCPConnection -State Listen -LocalPort $port `
            -OwningProcess $processId -ErrorAction SilentlyContinue
        if ($stillOwnsPort) {
            $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" `
                -ErrorAction Stop
            $isProjectPython = $owner.CommandLine -and $owner.CommandLine.Contains($venvPython)
            $isProjectApp = $owner.CommandLine -and $owner.CommandLine.Contains($appName)
            if (-not ($isProjectPython -and $isProjectApp)) {
                throw "Port $port is owned by unrelated PID $processId; it was not terminated."
            }
            Write-Host "Stopping verified ADE app on port $port (PID $processId)"
            Stop-Process -Id $processId -Force -ErrorAction Stop
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    while ((Get-NetTCPConnection -State Listen -LocalPort $port `
            -ErrorAction SilentlyContinue) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 100
    }
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
        throw "Port $port did not become available after stopping its verified owner."
    }

    & uv run --no-sync streamlit run streamlit_app.py --server.port $port `
        --server.address 127.0.0.1 --server.enableXsrfProtection true
    if ($LASTEXITCODE -ne 0) {
        throw "Streamlit exited with code $LASTEXITCODE."
    }
}
catch {
    Write-Error $_.Exception.Message
    exit 1
}
