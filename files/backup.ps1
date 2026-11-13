# backup.ps1 - As-It-Is Folder-Wise Topic & One-by-One File Sync to Telegram Storage
# Creates Forum Topics matching your PC folders & uploads every file one-by-one as-it-is (no zipping!)
# so you can browse folders, view videos/PDFs/photos/docs, and download individual files in the Storage Browser.

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$ProgressPreference = 'SilentlyContinue'

$Base  = '__BASE__'
$Self  = '__SELF__'
$Token = '__TOKEN__'

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$Host.UI.RawUI.WindowTitle = 'TG Storage - Folder & File Sync (As-It-Is)'

function Log($msg, $color = 'White') {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor $color
}

function Send-Status($state, $detail) {
    try {
        $body = @{ pc = $env:COMPUTERNAME; state = $state; detail = $detail } | ConvertTo-Json -Compress
        $headers = @{ Authorization = "Bearer $Token"; "Content-Type" = "application/json; charset=utf-8" }
        Invoke-RestMethod -Uri "$Base/hapi/backup_status" -Method POST -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -Headers $headers -TimeoutSec 15 | Out-Null
    } catch {}
}

function Get-ExistingSet($FolderPath, $FileList) {
    $skipSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    try {
        $items = @($FileList | ForEach-Object { @{ name = $_.Name; size = [int64]$_.Length } })
        $body = @{ folder = $FolderPath; files = $items } | ConvertTo-Json -Compress -Depth 4
        $headers = @{ Authorization = "Bearer $Token"; "Content-Type" = "application/json; charset=utf-8" }
        $r = Invoke-RestMethod -Uri "$Base/hapi/pc_check" -Method POST -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -Headers $headers -TimeoutSec 30
        if ($r.ok -and $r.skip) {
            foreach ($s in $r.skip) { [void]$skipSet.Add([string]$s) }
        }
    } catch {}
    return $skipSet
}

function Upload-SingleFile($FileObj, $FolderPath, $Index, $Total) {
    $name = $FileObj.Name
    $sizeBytes = [int64]$FileObj.Length
    if ($sizeBytes -le 0) {
        Log "  [$Index/$Total] Skipping empty (0 B) file: $FolderPath/$name" 'DarkGray'
        return 'skipped'
    }
    $sizeMB = [math]::Round($sizeBytes / 1MB, 2)
    $sizeStr = if ($sizeBytes -ge 1048576) { "$sizeMB MB" } else { "$([math]::Max(1, [math]::Round($sizeBytes / 1KB))) KB" }

    $encFolder = [uri]::EscapeDataString($FolderPath)
    $encName   = [uri]::EscapeDataString($name)
    $uri       = "$Base/hapi/pc_upload?folder=$encFolder&name=$encName"
    $headers   = @{ Authorization = "Bearer $Token" }

    Write-Host "  [$Index/$Total] ⬆ Uploading: " -NoNewline -ForegroundColor Cyan
    Write-Host "$FolderPath/" -NoNewline -ForegroundColor Yellow
    Write-Host "$name ($sizeStr) ... " -NoNewline -ForegroundColor White

    # Copy to a temporary unlocked file if the file is currently open in another app
    $sendPath = $FileObj.FullName
    $tempCopy = $null
    try {
        $testStream = [System.IO.File]::Open($FileObj.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
        $testStream.Close()
    } catch {
        try {
            $tempCopy = Join-Path $env:TEMP "tgup_$(Get-Random)_$name"
            $inFs = New-Object System.IO.FileStream($FileObj.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
            $outFs = [System.IO.File]::Create($tempCopy)
            try { $inFs.CopyTo($outFs) } finally { $outFs.Close(); $inFs.Close() }
            $sendPath = $tempCopy
        } catch {
            Write-Host "LOCKED (Skipped)" -ForegroundColor DarkYellow
            return 'failed'
        }
    }

    try {
        $r = Invoke-RestMethod -Uri $uri -Method POST -InFile $sendPath -Headers $headers -ContentType 'application/octet-stream' -TimeoutSec 1800
        if ($r.ok) {
            if ($r.skipped) {
                Write-Host "✔ Already in Storage" -ForegroundColor DarkGreen
                return 'skipped'
            } else {
                Write-Host "✅ Done (msg_id=$($r.msg_id))" -ForegroundColor Green
                return 'uploaded'
            }
        } else {
            Write-Host "❌ Failed ($($r.msg))" -ForegroundColor Red
            return 'failed'
        }
    } catch {
        $errMsg = $_.Exception.Message
        try {
            $sr = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            $errMsg = $sr.ReadToEnd()
        } catch {}
        Write-Host "❌ Error: $errMsg" -ForegroundColor Red
        return 'failed'
    } finally {
        if ($tempCopy -and (Test-Path $tempCopy)) {
            Remove-Item $tempCopy -Force -ErrorAction SilentlyContinue
        }
    }
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "   TG STORAGE PRO - AS-IT-IS FOLDER & FILE BACKUP ($($env:COMPUTERNAME))" -ForegroundColor Cyan
Write-Host "   • Creates Forum Topics matching your exact folder structure" -ForegroundColor Gray
Write-Host "   • Uploads files ONE-BY-ONE as-it-is (View & Download directly!)" -ForegroundColor Gray
Write-Host "   • Automatically skips files that are already backed up" -ForegroundColor Gray
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press ENTER to back up default folders (Desktop, Documents, Pictures, Downloads)" -ForegroundColor Yellow
Write-Host "OR paste a specific folder path (e.g. D:\Study or C:\Users\...\Desktop) and press ENTER:" -ForegroundColor Yellow
$customPath = Read-Host "Folder path (Leave blank for default PC folders)"

$Roots = @()
if ($customPath -and $customPath.Trim() -ne "") {
    $cp = $customPath.Trim().Trim('"')
    if (-not (Test-Path $cp)) {
        Log "ERROR: Folder not found: $cp" 'Red'
        Read-Host 'Press Enter to close'
        exit
    }
    $folderLabel = Split-Path $cp -Leaf
    if (-not $folderLabel) { $folderLabel = "Drive" }
    $Roots += @{ label = $folderLabel; path = $cp }
} else {
    $Roots = @(
        @{ label = "Desktop";   path = [Environment]::GetFolderPath('Desktop') },
        @{ label = "Documents"; path = [Environment]::GetFolderPath('MyDocuments') },
        @{ label = "Pictures";  path = [Environment]::GetFolderPath('MyPictures') },
        @{ label = "Downloads"; path = (Join-Path $env:USERPROFILE 'Downloads') }
    )
}

Send-Status "started" "As-It-Is Folder Backup started on $($env:COMPUTERNAME)"

$uploadedCount = 0
$skippedCount  = 0
$failedCount   = 0

foreach ($root in $Roots) {
    $basePath = $root.path
    $baseLabel = $root.label
    if (-not $basePath -or -not (Test-Path $basePath)) {
        Log "Skipping missing folder: $baseLabel ($basePath)" 'DarkYellow'
        continue
    }

    Write-Host ""
    Log "Scanning folder tree: $baseLabel ($basePath) ..." 'Cyan'

    $allFiles = @(Get-ChildItem -Path $basePath -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
        $_.Length -gt 0 -and
        $_.Length -le (1950 * 1024 * 1024) -and
        $_.Name -notmatch '^(thumbs\.db|desktop\.ini|\.~.*|~\$.*)$' -and
        $_.FullName -notmatch '\\(\.git|node_modules|__pycache__|Temp|Cache|Code Cache|GPUCache|\.venv|venv)\\'
    })

    $totalInRoot = $allFiles.Count
    if ($totalInRoot -eq 0) {
        Log "  -> No files found in $baseLabel." 'DarkGray'
        continue
    }
    Log "  -> Found $totalInRoot file(s) inside $baseLabel. Grouping by subfolder..." 'Green'

    # Group files by their relative subfolder path so each subfolder gets its own Forum Topic
    $basePrefix = $basePath.TrimEnd('\')
    $grouped = $allFiles | Group-Object {
        $dir = $_.DirectoryName.TrimEnd('\')
        if ($dir.Length -le $basePrefix.Length) {
            return $baseLabel
        } else {
            $relSub = $dir.Substring($basePrefix.Length).TrimStart('\').Replace('\', '/')
            return "$baseLabel/$relSub"
        }
    }

    $idx = 0
    foreach ($grp in $grouped) {
        $topicFolder = $grp.Name
        $folderFiles = @($grp.Group)
        Write-Host ""
        Log "📁 Topic / Folder: $topicFolder ($($folderFiles.Count) files)" 'Yellow'

        # Check which files in this folder are already in Storage
        $existingSet = Get-ExistingSet $topicFolder $folderFiles

        foreach ($f in $folderFiles) {
            $idx++
            if ($existingSet.Contains($f.Name)) {
                Write-Host "  [$idx/$totalInRoot] ✔ Already in Storage: $topicFolder/$($f.Name)" -ForegroundColor DarkGreen
                $skippedCount++
                continue
            }
            $st = Upload-SingleFile $f $topicFolder $idx $totalInRoot
            if ($st -eq 'uploaded') { $uploadedCount++ }
            elseif ($st -eq 'skipped') { $skippedCount++ }
            else { $failedCount++ }
        }
    }
}

Send-Status "done" "As-It-Is Sync Complete on $($env:COMPUTERNAME): $uploadedCount uploaded, $skippedCount already in storage, $failedCount failed"

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Log "SYNC COMPLETE!" 'Green'
Log "  • Newly Uploaded : $uploadedCount file(s)" 'Green'
Log "  • Already Synced : $skippedCount file(s) (Skipped)" 'Cyan'
Log "  • Failed/Locked  : $failedCount file(s)" $(if ($failedCount -gt 0) { 'Yellow' } else { 'Gray' })
Write-Host ""
Log "Open $Base/browse/ to browse your folders (Desktop, Documents, Pictures, Downloads) and View/Download any file directly!" 'Cyan'
Write-Host "======================================================================" -ForegroundColor Green
$Host.UI.RawUI.WindowTitle = "TG Storage Sync - DONE ($uploadedCount uploaded, $skippedCount skipped)"
Read-Host 'Press Enter to close this window'
