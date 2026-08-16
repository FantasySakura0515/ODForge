<#
.SYNOPSIS
  Launch the ODForge web console for local manual testing.

.DESCRIPTION
  Picks the venv that actually has the web extras, avoids ports other projects
  hold, checks the frontend build is not stale, starts `odforge serve` detached,
  waits for readiness, and reports which LLM backends are usable.

  State (pid / port / log paths) is written to $env:TEMP\odforge-dev-serve.json
  so -Stop and -Status can find the server later.
#>
[CmdletBinding()]
param(
  [int]$Port = 0,
  [switch]$Dev,
  [switch]$Rebuild,
  [switch]$Stop,
  [switch]$Status
)

$ErrorActionPreference = 'Stop'
# Backend "reason" strings are Traditional Chinese; without this the console
# renders them as mojibake on a cp950/cp437 default.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$repoRoot  = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$odforge   = Join-Path $repoRoot 'odforge'
$webDir    = Join-Path $odforge 'web'
$stateFile = Join-Path $env:TEMP 'odforge-dev-serve.json'

function Write-Head([string]$text) { Write-Host "`n$text" -ForegroundColor Cyan }
function Write-Ok  ([string]$text) { Write-Host "  OK   $text" -ForegroundColor Green }
function Write-Warn([string]$text) { Write-Host "  WARN $text" -ForegroundColor Yellow }
function Write-Bad ([string]$text) { Write-Host "  FAIL $text" -ForegroundColor Red }

function Get-Listener([int]$p) {
  try {
    return Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction Stop |
      Select-Object -First 1
  } catch { return $null }
}

function Get-ListenerLabel([int]$p) {
  $conn = Get-Listener $p
  if ($null -eq $conn) { return $null }
  try {
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$($conn.OwningProcess)").CommandLine
  } catch { $cmd = '' }
  if ([string]::IsNullOrWhiteSpace($cmd)) { $cmd = '(command line unavailable)' }
  return "PID $($conn.OwningProcess)  -  $cmd"
}

# /api/sources is the de-facto health endpoint: it is cheap, needs no LLM call,
# and its shape confirms we reached ODForge rather than some other local server.
function Get-Sources([int]$p) {
  try {
    # Not Invoke-RestMethod: PS 5.1 decodes charset-less JSON as latin-1, which
    # mangles the Chinese "reason" strings we print below.
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$p/api/sources" -TimeoutSec 4 -UseBasicParsing
    $body = [System.Text.Encoding]::UTF8.GetString($resp.RawContentStream.ToArray())
    return $body | ConvertFrom-Json
  } catch { return $null }
}

function Read-State {
  if (-not (Test-Path $stateFile)) { return $null }
  try { return Get-Content $stateFile -Raw | ConvertFrom-Json } catch { return $null }
}

# ---------------------------------------------------------------- -Stop --------
if ($Stop) {
  $state = Read-State
  if ($null -eq $state) {
    Write-Warn "no state file  -  nothing recorded as started by this script"
    exit 0
  }
  foreach ($name in @('pid', 'vitePid')) {
    $target = $state.$name
    if (-not $target) { continue }
    try {
      $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$target").CommandLine
    } catch { $cmd = $null }
    if ($null -eq $cmd) {
      Write-Warn "PID $target already gone ($name)"
      continue
    }
    # PIDs get recycled; only kill it if it still looks like ours.
    if ($cmd -notmatch 'odforge|vite|npm') {
      Write-Warn "PID $target is no longer ODForge  -  leaving it alone ($cmd)"
      continue
    }
    Stop-Process -Id $target -Force -Confirm:$false
    Write-Ok "stopped PID $target ($name)"
  }
  Remove-Item $stateFile -Force -Confirm:$false
  exit 0
}

# -------------------------------------------------------------- -Status --------
if ($Status) {
  $state = Read-State
  if ($null -eq $state) {
    Write-Host "no ODForge server recorded by this script."
  } else {
    $src = Get-Sources $state.port
    if ($null -eq $src) {
      Write-Bad "state says port $($state.port) (PID $($state.pid)) but it does not answer"
    } else {
      Write-Ok "ODForge live at http://127.0.0.1:$($state.port)/ (PID $($state.pid))"
      Write-Host "       log: $($state.log)"
    }
  }
  exit 0
}

# --------------------------------------------------------------- launch --------
Write-Head "ODForge: preflight"

# Only .venv312 carries the web extras (fastapi); .venv would fail at `serve`.
$exe = $null
foreach ($v in @('.venv312', '.venv')) {
  $candidate = Join-Path $odforge "$v\Scripts\odforge.exe"
  $python    = Join-Path $odforge "$v\Scripts\python.exe"
  if (-not (Test-Path $candidate)) { continue }
  & $python -c "import fastapi" 2>$null
  if ($LASTEXITCODE -eq 0) {
    $exe = $candidate
    Write-Ok "venv: $v (fastapi present)"
    break
  }
  Write-Warn "venv $v has no fastapi  -  skipping"
}
if ($null -eq $exe) {
  Write-Bad "no venv with web extras. Fix: .\odforge\.venv312\Scripts\pip install -e `".[web]`""
  exit 1
}

if (-not (Test-Path (Join-Path $odforge '.env'))) {
  Write-Warn "odforge\.env missing  -  cloud backends will report no API key"
} else {
  Write-Ok "odforge\.env found (serve loads it automatically)"
}

# Frontend build: `serve` mounts web/dist when present, giving a single-origin URL.
$distIndex = Join-Path $webDir 'dist\index.html'
if ($Rebuild -or -not (Test-Path $distIndex)) {
  Write-Head "ODForge: building frontend"
  Push-Location $webDir
  try {
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { Write-Bad "npm run build failed"; exit 1 }
  } finally { Pop-Location }
  Write-Ok "frontend built"
} else {
  $newestSrc = Get-ChildItem (Join-Path $webDir 'src') -Recurse -File |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  $builtAt = (Get-Item $distIndex).LastWriteTime
  if ($newestSrc -and $newestSrc.LastWriteTime -gt $builtAt) {
    Write-Warn "dist is older than web\src ($($newestSrc.Name) changed)  -  rerun with -Rebuild"
  } else {
    Write-Ok "frontend dist up to date"
  }
}

# Port selection. 8000 is routinely held by an unrelated local service, so we
# walk upward instead of fighting over it.
if ($Dev -and $Port -eq 0) { $Port = 8000 }  # vite.config.ts proxies to :8000
if ($Port -eq 0) {
  foreach ($p in 8000..8020) {
    $existing = Get-Sources $p
    if ($null -ne $existing) {
      Write-Ok "ODForge already serving on $p  -  reusing it"
      Write-Host "`n  ->  http://127.0.0.1:$p/`n" -ForegroundColor White
      exit 0
    }
    if ($null -eq (Get-Listener $p)) { $Port = $p; break }
  }
}
if ($Port -eq 0) { Write-Bad "no free port in 8000-8020"; exit 1 }

$holder = Get-ListenerLabel $Port
if ($null -ne $holder) {
  $existing = Get-Sources $Port
  if ($null -ne $existing) {
    Write-Ok "ODForge already serving on $Port  -  reusing it"
    Write-Host "`n  ->  http://127.0.0.1:$Port/`n" -ForegroundColor White
    exit 0
  }
  Write-Bad "port $Port is held by something else: $holder"
  if ($Dev) {
    Write-Host "       -Dev needs :8000 because web\vite.config.ts proxies there." -ForegroundColor Yellow
    Write-Host "       Options: drop -Dev (prod mode picks a free port), free :8000," -ForegroundColor Yellow
    Write-Host "       or repoint the proxy in web\vite.config.ts." -ForegroundColor Yellow
  } else {
    Write-Host "       Pass -Port <n> to choose another." -ForegroundColor Yellow
  }
  exit 1
}

Write-Head "ODForge: starting API on $Port"
$log    = Join-Path $env:TEMP "odforge-serve-$Port.log"
$errLog = "$log.err"
$proc = Start-Process -FilePath $exe -ArgumentList @('serve', '--port', "$Port") `
  -WorkingDirectory $odforge -RedirectStandardOutput $log -RedirectStandardError $errLog `
  -WindowStyle Hidden -PassThru

$sources = $null
foreach ($attempt in 1..30) {
  if ($proc.HasExited) {
    Write-Bad "server exited with code $($proc.ExitCode)"
    Write-Host "       stderr tail:" -ForegroundColor Yellow
    if (Test-Path $errLog) { Get-Content $errLog -Tail 12 | ForEach-Object { Write-Host "         $_" } }
    exit 1
  }
  $sources = Get-Sources $Port
  if ($null -ne $sources) { break }
  Start-Sleep -Milliseconds 600
}
if ($null -eq $sources) {
  Write-Bad "server did not answer within ~18s. Log: $log"
  exit 1
}
Write-Ok "API ready (PID $($proc.Id))"

$state = [ordered]@{ pid = $proc.Id; port = $Port; log = $log; errLog = $errLog }

# ------------------------------------------------------------- vite dev --------
if ($Dev) {
  Write-Head "ODForge: starting vite dev server"
  $viteLog = Join-Path $env:TEMP 'odforge-vite.log'
  $vite = Start-Process -FilePath 'npm.cmd' -ArgumentList @('run', 'dev') `
    -WorkingDirectory $webDir -RedirectStandardOutput $viteLog `
    -RedirectStandardError "$viteLog.err" -WindowStyle Hidden -PassThru
  $state.vitePid = $vite.Id
  $state.viteLog = $viteLog
  foreach ($attempt in 1..30) {
    try {
      Invoke-WebRequest -Uri 'http://localhost:5173/' -TimeoutSec 3 -UseBasicParsing | Out-Null
      break
    } catch { Start-Sleep -Milliseconds 600 }
  }
  Write-Ok "vite dev server ready (PID $($vite.Id))"
}

[pscustomobject]$state | ConvertTo-Json | Set-Content -Path $stateFile -Encoding utf8

# ------------------------------------------------------------- report ---------
Write-Head "ODForge: backends"
$textOk   = ($sources.text   | Where-Object { $_.available }).name -join ', '
$visionOk = ($sources.vision | Where-Object { $_.available }).name -join ', '
Write-Host "  text   available: $textOk   (default: $($sources.defaults.text))"
Write-Host "  vision available: $visionOk   (default: $($sources.defaults.vision))"
if ($sources.defaults.vision -eq 'off') {
  Write-Warn "vision default is off  -  the design gate renders pages but nothing reviews them, so it always reports zero findings"
}
foreach ($b in @($sources.text) + @($sources.vision)) {
  if (-not $b.available -and $b.reason) { Write-Host "         $($b.name): $($b.reason)" -ForegroundColor DarkGray }
}

Write-Head "ODForge: ready"
if ($Dev) {
  Write-Host "  ->  frontend (HMR)  http://localhost:5173/" -ForegroundColor White
  Write-Host "     API             http://127.0.0.1:$Port/" -ForegroundColor White
} else {
  Write-Host "  ->  http://127.0.0.1:$Port/" -ForegroundColor White
}
Write-Host "     log   $log"
Write-Host "     stop  powershell -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Stop`n"
