<#
  KORA - downloads whatever the app needs and does not have yet:
    1. chat model        data\models\Qwen3.5-0.8B-Q4_K_M.gguf
    2. embedding model   data\models\nomic-embed-text-v1.5.Q4_K_M.gguf
    3. llama.cpp runtime data\runtime\vulkan\llama-server.exe (official Windows Vulkan build)
    4. Tesseract OCR     installed with winget, folder added to your PATH   (optional, for scanned files and images)
    5. LibreOffice       installed with winget, folder added to your PATH   (optional, document conversion)
  Tesseract and LibreOffice need administrator rights, so Windows shows one permission prompt for each.
  Files that are already present and valid are skipped, so it is safe to run again at any time.
  Called by setup_kora.bat and run_kora.bat. Exit code 0 = everything required is present, 1 = a model or the
  runtime failed. Problems with the two optional tools only print a warning.

  Optional environment variables:
    KORA_CHAT_MODEL / KORA_EMBED_MODEL          use a different file name (same as config.py)
    KORA_CHAT_MODEL_URL / KORA_EMBED_MODEL_URL  where to download that file from
#>
$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

$Root       = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) '..')).Path
$ModelsDir  = Join-Path $Root 'data\models'
$RuntimeDir = Join-Path $Root 'data\runtime'
$VulkanDir  = Join-Path $RuntimeDir 'vulkan'
foreach ($d in @($ModelsDir, $RuntimeDir, $VulkanDir)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

$DefaultChat  = 'Qwen3.5-0.8B-Q4_K_M.gguf'
$DefaultEmbed = 'nomic-embed-text-v1.5.Q4_K_M.gguf'
$ChatUrl      = 'https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/main/Qwen3.5-0.8B-Q4_K_M.gguf'
$EmbedUrl     = 'https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/nomic-embed-text-v1.5.Q4_K_M.gguf'
$LlamaLatest  = 'https://api.github.com/repos/ggml-org/llama.cpp/releases/latest'
$LlamaPinned  = 'https://github.com/ggml-org/llama.cpp/releases/download/b11135/llama-b11135-bin-win-vulkan-x64.zip'

$script:Failed = $false

function Write-Info($m) { Write-Host "[INFO] $m" }
function Write-Ok($m)   { Write-Host "[ OK ] $m" -ForegroundColor Green }
function Write-Warn($m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Write-Fail($m) { Write-Host "[FAIL] $m" -ForegroundColor Red; $script:Failed = $true }

function Test-Gguf([string]$Path, [long]$MinBytes) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    if ((Get-Item -LiteralPath $Path).Length -lt $MinBytes) { return $false }
    try {
        $fs = [IO.File]::OpenRead($Path)
        try { $b = New-Object byte[] 4; [void]$fs.Read($b, 0, 4) } finally { $fs.Dispose() }
        return ([Text.Encoding]::ASCII.GetString($b) -eq 'GGUF')
    } catch { return $false }
}

function Get-File([string]$Url, [string]$Dest) {
    $part = "$Dest.part"
    $done = $false
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        # curl.exe ships with Windows 10/11; it shows progress and can resume an interrupted download.
        & $curl.Source -L --fail --retry 5 --retry-delay 3 -C - --progress-bar -o $part $Url
        if ($LASTEXITCODE -eq 0) { $done = $true }
        else { Write-Warn "curl exited with code $LASTEXITCODE, retrying with PowerShell."; Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
    }
    if (-not $done) {
        Write-Info 'Downloading (no progress bar in this mode, please wait)...'
        Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $part
    }
    if (Test-Path -LiteralPath $Dest) { Remove-Item -LiteralPath $Dest -Force }
    Move-Item -LiteralPath $part -Destination $Dest
}

function Install-Model([string]$Label, [string]$EnvName, [string]$Default, [string]$DefaultUrl, [long]$MinBytes) {
    $name = [Environment]::GetEnvironmentVariable($EnvName)
    if ([string]::IsNullOrWhiteSpace($name)) { $name = $Default }
    $dest = Join-Path $ModelsDir $name
    if (Test-Gguf $dest $MinBytes) { Write-Ok "$Label already present: $name"; return }

    $url = $null
    if ($name -eq $Default) { $url = $DefaultUrl }
    $override = [Environment]::GetEnvironmentVariable($EnvName + '_URL')
    if (-not [string]::IsNullOrWhiteSpace($override)) { $url = $override }
    if (-not $url) {
        Write-Fail "$Label '$name' is configured but missing, and no download address is known. Put it in data\models\ or set $($EnvName)_URL."
        return
    }
    if (Test-Path -LiteralPath $dest) { Write-Warn "$name looks incomplete or corrupt. Downloading it again."; Remove-Item -LiteralPath $dest -Force }

    Write-Info "Downloading $Label ($name)..."
    try {
        Get-File $url $dest
        if (Test-Gguf $dest $MinBytes) { Write-Ok "$Label downloaded: $name" }
        else { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue; Write-Fail "$Label download finished but the file is not a valid GGUF model." }
    } catch {
        Write-Fail "$Label download failed: $($_.Exception.Message)"
        Write-Host "       Download it manually from: $url"
        Write-Host "       and save it as: $dest"
    }
}

function Test-Runtime {
    foreach ($sub in @('vulkan', 'cuda', 'sycl', 'cpu', '')) {
        $p = if ($sub) { Join-Path $RuntimeDir "$sub\llama-server.exe" } else { Join-Path $RuntimeDir 'llama-server.exe' }
        if (Test-Path -LiteralPath $p) { return $true }
    }
    return $false
}

function Install-Runtime {
    if (Test-Runtime) { Write-Ok 'llama.cpp runtime already present (data\runtime)'; return }
    Write-Info 'Downloading the llama.cpp Vulkan runtime (official Windows build)...'
    $zip = Join-Path $RuntimeDir '_llama_vulkan.zip'
    $tmp = Join-Path $RuntimeDir '_llama_vulkan_extract'
    try {
        $url = $LlamaPinned
        try {
            $rel = Invoke-RestMethod -Uri $LlamaLatest -Headers @{ 'User-Agent' = 'KORA-setup' } -TimeoutSec 30
            $asset = $rel.assets | Where-Object { $_.name -like 'llama-*-bin-win-vulkan-x64.zip' } | Select-Object -First 1
            if ($asset) { $url = $asset.browser_download_url; Write-Info "Latest release: $($rel.tag_name)" }
            else { Write-Warn 'Latest release has no Windows Vulkan build; using the pinned version.' }
        } catch {
            Write-Warn 'Could not query the latest llama.cpp release; using the pinned version.'
        }

        Get-File $url $zip
        if ((Get-Item -LiteralPath $zip).Length -lt 5MB) { throw 'The downloaded archive is too small to be valid.' }

        if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
        Expand-Archive -LiteralPath $zip -DestinationPath $tmp -Force
        $server = Get-ChildItem -LiteralPath $tmp -Recurse -Filter 'llama-server.exe' | Select-Object -First 1
        if (-not $server) { throw 'llama-server.exe was not found inside the archive.' }
        Copy-Item -Path (Join-Path $server.DirectoryName '*') -Destination $VulkanDir -Recurse -Force
        if (Test-Path -LiteralPath (Join-Path $VulkanDir 'llama-server.exe')) { Write-Ok 'llama.cpp Vulkan runtime installed: data\runtime\vulkan' }
        else { throw 'Copying the runtime files failed.' }
    } catch {
        Write-Fail "Runtime download failed: $($_.Exception.Message)"
        Write-Host '       Download llama-<build>-bin-win-vulkan-x64.zip from https://github.com/ggml-org/llama.cpp/releases'
        Write-Host "       and extract it into: $VulkanDir"
    } finally {
        Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath "$zip.part" -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Install-Model 'chat model'      'KORA_CHAT_MODEL'  $DefaultChat  $ChatUrl  300MB
Install-Model 'embedding model' 'KORA_EMBED_MODEL' $DefaultEmbed $EmbedUrl 60MB
Install-Runtime

# ---------------------------------------------------------------- optional tools: Tesseract OCR, LibreOffice
function Find-Tool([string]$Exe, [string[]]$Folders) {
    $cmd = Get-Command $Exe -ErrorAction SilentlyContinue
    if ($cmd) { return (Split-Path -Parent $cmd.Source) }
    foreach ($f in $Folders) {
        if ($f -and (Test-Path -LiteralPath (Join-Path $f $Exe))) { return $f }
    }
    return $null
}

function Add-ToUserPath([string]$Folder) {
    $norm = $Folder.TrimEnd('\')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $parts = @()
    if ($user) { $parts = $user.Split(';') | Where-Object { $_ } }
    if (-not ($parts | Where-Object { $_.TrimEnd('\') -ieq $norm })) {
        $new = (($parts + $norm) -join ';')
        [Environment]::SetEnvironmentVariable('Path', $new, 'User')   # also tells Windows the setting changed
        Write-Ok "Added to your PATH: $norm"
    } else {
        Write-Ok "Already on your PATH: $norm"
    }
    if (-not (($env:Path.Split(';')) | Where-Object { $_.TrimEnd('\') -ieq $norm })) { $env:Path = "$env:Path;$norm" }
}

function Install-Tool([string]$Label, [string]$Exe, [string]$WingetId, [string[]]$Folders, [string]$ManualUrl) {
    $dir = Find-Tool $Exe $Folders
    if (-not $dir) {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) {
            Write-Warn "$Label is not installed and winget is not available on this PC. Install it from $ManualUrl, then run setup_kora.bat again."
            return
        }
        Write-Info "Installing $Label with winget. Windows may ask for permission, please click Yes..."
        try {
            & $winget.Source install --id $WingetId -e --source winget --silent --accept-package-agreements --accept-source-agreements
        } catch {
            Write-Warn "winget could not install ${Label}: $($_.Exception.Message)"
        }
        $dir = Find-Tool $Exe $Folders
        if (-not $dir) {
            Write-Warn "$Label was not installed (permission refused or the install failed). It is optional. Install it from $ManualUrl and run setup_kora.bat again."
            return
        }
        Write-Ok "$Label installed."
    } else {
        Write-Ok "$Label already installed: $dir"
    }
    Add-ToUserPath $dir
}

$pf   = $env:ProgramFiles
$pf86 = [Environment]::GetEnvironmentVariable('ProgramFiles(x86)')
$lad  = $env:LOCALAPPDATA
Install-Tool 'Tesseract OCR' 'tesseract.exe' 'UB-Mannheim.TesseractOCR' @("$pf\Tesseract-OCR", "$pf86\Tesseract-OCR", "$lad\Programs\Tesseract-OCR") 'https://github.com/UB-Mannheim/tesseract/wiki'
Install-Tool 'LibreOffice'   'soffice.exe'   'TheDocumentFoundation.LibreOffice' @("$pf\LibreOffice\program", "$pf86\LibreOffice\program", "$lad\Programs\LibreOffice\program") 'https://www.libreoffice.org/download/'

if ($script:Failed) { exit 1 }
exit 0
