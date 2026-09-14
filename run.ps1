# Single-command end-to-end launcher for Urban Cool (PowerShell version).
# Starts the FastAPI backend and Next.js frontend, waits for BOTH to actually
# respond, then tails both logs until Ctrl+C, at which point it stops both.

# NOT "Stop": uvicorn/npm write normal INFO logs to stderr, and background jobs
# surface stderr lines as PowerShell error records. With ErrorActionPreference
# set to Stop, receiving those records through the tail loop below would abort
# this whole script the instant the backend logged its first INFO line.
$ErrorActionPreference = "Continue"

$RootDir = $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$LogDir = Join-Path $RootDir ".run-logs"
$PythonBin = "C:\Users\ckkav\AppData\Local\Programs\Python\Python311\python.exe"

$BackendHost = "127.0.0.1"
$BackendPort = 8000
$FrontendPort = 3000
$BackendUrl = "http://${BackendHost}:${BackendPort}"
$FrontendUrl = "http://localhost:${FrontendPort}"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$BackendLog = Join-Path $LogDir "backend.log"
$FrontendLog = Join-Path $LogDir "frontend.log"

function Free-Port($port) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($conns) {
        $pids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($procId in $pids) {
            Write-Host "Freeing port $port (killing PID $procId)"
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-ForHttp($url, $label, $timeoutSeconds) {
    Write-Host "Waiting for $label at $url ..."
    $waited = 0
    while ($true) {
        try {
            $resp = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                Write-Host "$label is up (took ${waited}s)."
                return $true
            }
        } catch {
            # not up yet, keep waiting
        }
        Start-Sleep -Seconds 1
        $waited++
        if ($waited -ge $timeoutSeconds) {
            Write-Host "TIMEOUT: $label did not respond within ${timeoutSeconds}s. Check $LogDir."
            return $false
        }
    }
}

$backendJob = $null
$frontendJob = $null

function Cleanup {
    Write-Host ""
    Write-Host "Shutting down..."
    if ($backendJob) { Stop-Job $backendJob -ErrorAction SilentlyContinue; Remove-Job $backendJob -Force -ErrorAction SilentlyContinue }
    if ($frontendJob) { Stop-Job $frontendJob -ErrorAction SilentlyContinue; Remove-Job $frontendJob -Force -ErrorAction SilentlyContinue }
    Free-Port $FrontendPort
    Free-Port $BackendPort
    Write-Host "Stopped."
}

Write-Host "====================================================="
Write-Host "Urban Cool -- end-to-end launch"
Write-Host "====================================================="

Write-Host ""
Write-Host "Step 1/4: freeing ports $BackendPort and $FrontendPort if already in use ..."
Free-Port $BackendPort
Free-Port $FrontendPort

Write-Host ""
Write-Host "Step 2/4: starting backend (this loads the cached feature stack + trained model into memory) ..."
$backendJob = Start-Job -ScriptBlock {
    param($dir, $py, $bHost, $bPort)
    Set-Location $dir
    & $py -m uvicorn app.main:app --host $bHost --port $bPort
} -ArgumentList $BackendDir, $PythonBin, $BackendHost, $BackendPort

Start-Sleep -Seconds 2
Receive-Job $backendJob -Keep -ErrorAction SilentlyContinue 2>&1 | ForEach-Object { $_.ToString() } | Out-File -FilePath $BackendLog -Encoding utf8

if (-not (Wait-ForHttp "$BackendUrl/api/v1/health" "Backend" 60)) {
    Receive-Job $backendJob -Keep -ErrorAction SilentlyContinue 2>&1 | ForEach-Object { $_.ToString() } | Out-File -FilePath $BackendLog -Encoding utf8
    Write-Host "--- last 30 lines of backend log ---"
    Get-Content $BackendLog -Tail 30
    Cleanup
    exit 1
}

Write-Host ""
Write-Host "Step 3/4: starting frontend ..."
$frontendJob = Start-Job -ScriptBlock {
    param($dir)
    Set-Location $dir
    npm run dev
} -ArgumentList $FrontendDir

Start-Sleep -Seconds 2
Receive-Job $frontendJob -Keep -ErrorAction SilentlyContinue 2>&1 | ForEach-Object { $_.ToString() } | Out-File -FilePath $FrontendLog -Encoding utf8

if (-not (Wait-ForHttp $FrontendUrl "Frontend" 60)) {
    Receive-Job $frontendJob -Keep -ErrorAction SilentlyContinue 2>&1 | ForEach-Object { $_.ToString() } | Out-File -FilePath $FrontendLog -Encoding utf8
    Write-Host "--- last 30 lines of frontend log ---"
    Get-Content $FrontendLog -Tail 30
    Cleanup
    exit 1
}

Write-Host ""
Write-Host "Step 4/4: ready."
Write-Host "====================================================="
Write-Host "Backend:  $BackendUrl  (docs at $BackendUrl/docs)"
Write-Host "Frontend: $FrontendUrl"
Write-Host "Logs:     $LogDir\"
Write-Host "====================================================="
Write-Host "Press Ctrl+C to stop both."
Write-Host ""

try {
    while ($true) {
        Receive-Job $backendJob -ErrorAction SilentlyContinue 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $BackendLog -Append
        Receive-Job $frontendJob -ErrorAction SilentlyContinue 2>&1 | ForEach-Object { $_.ToString() } | Tee-Object -FilePath $FrontendLog -Append
        Start-Sleep -Seconds 1
    }
} finally {
    Cleanup
}
