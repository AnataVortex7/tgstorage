# backup.ps1 - Small-First + Chunked Big-File As-It-Is Folder Sync to Telegram Storage
# 1. Scans folders & checks which files are already in Storage
# 2. Uploads ALL SMALL FILES FIRST (sorted smallest -> largest), then BIG FILES!
# 3. Every file is placed AS-IT-IS into its exact folder Forum Topic
# 4. Big files (> 4 MB, up to 2 GB) use 8 MB Resumable Chunks + Live Async Progress so connections NEVER drop!

$ErrorActionPreference = 'Continue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
[Net.ServicePointManager]::Expect100Continue = $false
[Net.ServicePointManager]::DefaultConnectionLimit = 24
$ProgressPreference = 'SilentlyContinue'

$Base  = '__BASE__'
$Self  = '__SELF__'
$Token = '__TOKEN__'

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = '1'
$Host.UI.RawUI.WindowTitle = 'TG Storage - Small-First + Big-File Folder Sync'

function Log($msg, $color = 'White') {
    Write-Host "[$(Get-Date -Format 'HH:mm:ss')] $msg" -ForegroundColor $color
}

function Format-FileSize([int64]$bytes) {
    if ($bytes -ge 1073741824) { return "$([math]::Round($bytes / 1GB, 2)) GB" }
    if ($bytes -ge 1048576)    { return "$([math]::Round($bytes / 1MB, 2)) MB" }
    return "$([math]::Max(1, [math]::Round($bytes / 1KB))) KB"
}

function Get-HttpErrorDetail($ErrRecord) {
    $msg = $ErrRecord.Exception.Message
    if ($ErrRecord.ErrorDetails -and $ErrRecord.ErrorDetails.Message) {
        return "$msg -> $($ErrRecord.ErrorDetails.Message)"
    }
    try {
        $resp = $ErrRecord.Exception.Response
        if ($resp) {
            $code = [int]$resp.StatusCode
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            $txt = $sr.ReadToEnd()
            if ($txt -and $txt.Trim() -ne "") {
                return "HTTP $code : $($txt.Trim())"
            }
            return "HTTP $code ($msg)"
        }
    } catch {}
    return $msg
}

function Send-Status($state, $detail) {
    try {
        $body = @{ pc = $env:COMPUTERNAME; state = $state; detail = $detail } | ConvertTo-Json -Compress
        $headers = @{ Authorization = "Bearer $Token"; "Content-Type" = "application/json; charset=utf-8" }
        Invoke-RestMethod -Uri "$Base/hapi/backup_status" -Method POST -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -Headers $headers -TimeoutSec 15 -DisableKeepAlive | Out-Null
    } catch {}
}

function Get-ExistingMap($FolderPath, $FileList) {
    # Hashtable is NEVER unrolled to $null by PowerShell even when empty!
    $skipMap = @{}
    $total = $FileList.Count
    $batchSize = 400
    for ($offset = 0; $offset -lt $total; $offset += $batchSize) {
        $end = [math]::Min($offset + $batchSize - 1, $total - 1)
        $slice = $FileList[$offset..$end]
        try {
            $items = @($slice | ForEach-Object { @{ name = $_.Name; size = [int64]$_.Length } })
            $body = @{ folder = $FolderPath; files = $items } | ConvertTo-Json -Compress -Depth 4
            $headers = @{ Authorization = "Bearer $Token"; "Content-Type" = "application/json; charset=utf-8" }
            $r = Invoke-RestMethod -Uri "$Base/hapi/pc_check" -Method POST -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -Headers $headers -TimeoutSec 30 -DisableKeepAlive
            if ($r -and $r.ok -and $r.skip) {
                foreach ($s in @($r.skip)) {
                    if ($null -ne $s) {
                        $skipMap[([string]$s).ToLower()] = $true
                    }
                }
            }
        } catch {}
    }
    return $skipMap
}

function Read-FileBytesSafe($FullPath) {
    $inFs = New-Object System.IO.FileStream($FullPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
    try {
        $ms = New-Object System.IO.MemoryStream
        $inFs.CopyTo($ms)
        return ,$ms.ToArray()
    } finally {
        $inFs.Dispose()
    }
}

function Compress-GzipBytes($RawBytes) {
    $outMs = New-Object System.IO.MemoryStream
    $gz = New-Object System.IO.Compression.GZipStream($outMs, [System.IO.Compression.CompressionMode]::Compress)
    $gz.Write($RawBytes, 0, $RawBytes.Length)
    $gz.Close()
    return ,$outMs.ToArray()
}

function Upload-ChunkedBigFile($FileObj, $FolderPath, $Index, $Total) {
    $name = $FileObj.Name
    $sizeBytes = [int64]$FileObj.Length
    $sizeStr = Format-FileSize $sizeBytes

    Write-Host "  [$Index/$Total] 📦 Big File: " -NoNewline -ForegroundColor Magenta
    Write-Host "$FolderPath/" -NoNewline -ForegroundColor Yellow
    Write-Host "$name ($sizeStr)" -ForegroundColor White

    $headersJson = @{ Authorization = "Bearer $Token"; "Content-Type" = "application/json; charset=utf-8" }
    $startBody = @{ folder = $FolderPath; name = $name; size = $sizeBytes } | ConvertTo-Json -Compress
    $startResp = $null
    try {
        $startResp = Invoke-RestMethod -Uri "$Base/hapi/pc_up_start" -Method POST -Body ([System.Text.Encoding]::UTF8.GetBytes($startBody)) -Headers $headersJson -TimeoutSec 30 -DisableKeepAlive
    } catch {
        $err = Get-HttpErrorDetail $_
        Write-Host "     ❌ Failed to start chunked upload: $err" -ForegroundColor Red
        return 'failed'
    }

    if (-not $startResp.ok) {
        Write-Host "     ❌ Server rejected upload start: $($startResp.msg)" -ForegroundColor Red
        return 'failed'
    }
    if ($startResp.skipped) {
        Write-Host "     ✔ Already in Storage (msg_id=$($startResp.msg_id))" -ForegroundColor DarkGreen
        return 'skipped'
    }

    $jobId = [string]$startResp.id
    $chunkSize = if ($startResp.chunk -and [int]$startResp.chunk -gt 0) { [int]$startResp.chunk } else { 8 * 1024 * 1024 }

    $inFs = $null
    try {
        $inFs = New-Object System.IO.FileStream($FileObj.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
    } catch {
        Write-Host "     ❌ Locked/Unreadable file: $($_.Exception.Message)" -ForegroundColor Red
        return 'failed'
    }

    try {
        $buf = New-Object byte[] $chunkSize
        $offset = [int64]0
        while ($offset -lt $sizeBytes) {
            $want = [int][math]::Min([int64]$chunkSize, $sizeBytes - $offset)
            $inFs.Position = $offset
            $readCount = $inFs.Read($buf, 0, $want)
            if ($readCount -le 0) { break }

            $chunkBytes = if ($readCount -eq $buf.Length) { $buf } else {
                $tmpB = New-Object byte[] $readCount
                [Array]::Copy($buf, 0, $tmpB, 0, $readCount)
                $tmpB
            }

            $chunkOk = $false
            $lastErr = ""
            for ($try = 1; $try -le 6; $try++) {
                try {
                    $cUri = "$Base/hapi/pc_up_chunk?id=$jobId&off=$offset"
                    $cResp = Invoke-RestMethod -Uri $cUri -Method POST -Body $chunkBytes -Headers @{ Authorization = "Bearer $Token" } -ContentType 'application/octet-stream' -TimeoutSec 180 -DisableKeepAlive
                    if ($cResp.ok) {
                        $offset = [int64]$cResp.written
                        $chunkOk = $true
                        break
                    } else {
                        $lastErr = [string]$cResp.msg
                    }
                } catch {
                    $lastErr = Get-HttpErrorDetail $_
                    # If offset mismatch (409), sync offset from server response
                    try {
                        $respObj = $_.ErrorDetails.Message | ConvertFrom-Json
                        if ($null -ne $respObj.written) {
                            $offset = [int64]$respObj.written
                            $chunkOk = $true
                            break
                        }
                    } catch {}
                }
                Write-Host "`r     ⚠️ Network hiccup on chunk at $(Format-FileSize $offset) (Retry $try/6)...          " -NoNewline -ForegroundColor Yellow
                Start-Sleep -Seconds ($try * 2)
            }

            if (-not $chunkOk) {
                Write-Host ""
                Write-Host "     ❌ Chunk upload failed at $(Format-FileSize $offset): $lastErr" -ForegroundColor Red
                return 'failed'
            }

            $pct = [math]::Min(100, [math]::Round(($offset * 100.0) / $sizeBytes))
            Write-Host "`r     ↳ 📡 Step 1/2 (PC -> Server 8MB Chunks): $pct% ($(Format-FileSize $offset) / $sizeStr)   " -NoNewline -ForegroundColor Cyan
        }
        Write-Host ""
    } finally {
        $inFs.Dispose()
    }

    # Step 2: Trigger async server -> Telegram upload and poll status
    try {
        $finBody = @{ id = $jobId } | ConvertTo-Json -Compress
        $finResp = Invoke-RestMethod -Uri "$Base/hapi/pc_up_finish" -Method POST -Body ([System.Text.Encoding]::UTF8.GetBytes($finBody)) -Headers $headersJson -TimeoutSec 30 -DisableKeepAlive
        if (-not $finResp.ok) {
            Write-Host "     ❌ Finish call failed: $($finResp.msg)" -ForegroundColor Red
            return 'failed'
        }
    } catch {
        Write-Host "     ❌ Finish call error: $(Get-HttpErrorDetail $_)" -ForegroundColor Red
        return 'failed'
    }

    while ($true) {
        Start-Sleep -Seconds 2
        try {
            $stResp = Invoke-RestMethod -Uri "$Base/hapi/pc_up_status?id=$jobId" -Method GET -Headers @{ Authorization = "Bearer $Token" } -TimeoutSec 20 -DisableKeepAlive
            if (-not $stResp.ok) {
                Write-Host ""
                Write-Host "     ❌ Status error: $($stResp.msg)" -ForegroundColor Red
                return 'failed'
            }
            if ($stResp.state -eq 'done') {
                Write-Host "`r     ↳ ☁️ Step 2/2 (Server -> Telegram Topic [$FolderPath]): 100% ($sizeStr) ... ✅ Done (msg_id=$($stResp.msg_id))   " -ForegroundColor Green
                return 'uploaded'
            }
            if ($stResp.state -eq 'error') {
                Write-Host ""
                Write-Host "     ❌ Telegram upload error: $($stResp.err)" -ForegroundColor Red
                return 'failed'
            }
            if ($stResp.flood_wait -and [int]$stResp.flood_wait -gt 0) {
                Write-Host "`r     ↳ ⏳ Telegram FloodWait: Server pausing $($stResp.flood_wait)s then auto-resuming...                " -NoNewline -ForegroundColor Yellow
            } else {
                $tgSent = [int64]($stResp.tg_sent)
                $tgPct  = [math]::Min(99, [math]::Round(($tgSent * 100.0) / $sizeBytes))
                Write-Host "`r     ↳ ☁️ Step 2/2 (Server -> Telegram Topic [$FolderPath]): $tgPct% ($(Format-FileSize $tgSent) / $sizeStr)   " -NoNewline -ForegroundColor Yellow
            }
        } catch {
            # Ignore transient status poll glitch
        }
    }
}

function Upload-SingleFile($FileObj, $FolderPath, $Index, $Total) {
    $name = $FileObj.Name
    $sizeBytes = [int64]$FileObj.Length
    if ($sizeBytes -le 0) {
        Log "  [$Index/$Total] Skipping empty (0 B) file: $FolderPath/$name" 'DarkGray'
        return 'skipped'
    }

    # Any file > 4 MB uses the 8 MB Chunked Resumable Upload + Async Telegram Progress engine!
    if ($sizeBytes -gt (4 * 1024 * 1024)) {
        return (Upload-ChunkedBigFile $FileObj $FolderPath $Index $Total)
    }

    $sizeStr = Format-FileSize $sizeBytes
    $encFolder = [uri]::EscapeDataString($FolderPath)
    $encName   = [uri]::EscapeDataString($name)

    Write-Host "  [$Index/$Total] ⬆ Small File: " -NoNewline -ForegroundColor Cyan
    Write-Host "$FolderPath/" -NoNewline -ForegroundColor Yellow
    Write-Host "$name ($sizeStr) ... " -NoNewline -ForegroundColor White

    $payloadBytes = $null
    try {
        $rawBytes = Read-FileBytesSafe $FileObj.FullName
        $payloadBytes = Compress-GzipBytes $rawBytes
    } catch {
        Write-Host "LOCKED/UNREADABLE ($($_.Exception.Message))" -ForegroundColor DarkYellow
        return 'failed'
    }

    $lastErr = ""
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            $uri = "$Base/hapi/pc_upload?folder=$encFolder&name=$encName&gz=1&orig_size=$sizeBytes"
            $headers = @{ Authorization = "Bearer $Token"; "X-Backup-Gz" = "1" }
            $r = Invoke-RestMethod -Uri $uri -Method POST -Body $payloadBytes -Headers $headers -ContentType 'application/octet-stream' -TimeoutSec 180 -DisableKeepAlive
            if ($r.ok) {
                if ($r.skipped) {
                    Write-Host "✔ Already in Storage" -ForegroundColor DarkGreen
                    return 'skipped'
                } else {
                    Write-Host "✅ Done (msg_id=$($r.msg_id))" -ForegroundColor Green
                    Start-Sleep -Milliseconds 350
                    return 'uploaded'
                }
            } elseif ($r.retry_after -and [int]$r.retry_after -gt 0) {
                $waitSec = [int]$r.retry_after
                Write-Host ""
                for ($rem = $waitSec; $rem -gt 0; $rem--) {
                    Write-Host "`r    ⏳ Telegram rate-limit pause: waiting $rem s before continuing...   " -NoNewline -ForegroundColor Yellow
                    Start-Sleep -Seconds 1
                }
                Write-Host "`r    🔄 Resuming upload of $name...                                       " -ForegroundColor Cyan
                continue
            } else {
                $lastErr = [string]$r.msg
            }
        } catch {
            $lastErr = Get-HttpErrorDetail $_
        }
        if ($attempt -lt 5) {
            Start-Sleep -Seconds ($attempt * 2)
        }
    }
    Write-Host "❌ Error: $lastErr" -ForegroundColor Red
    return 'failed'
}

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "   TG STORAGE PRO - SMALL-FIRST + BIG-FILE AS-IT-IS FOLDER SYNC" -ForegroundColor Cyan
Write-Host "   • Phase 1: Backs up ALL SMALL files first (sorted smallest -> largest)" -ForegroundColor Gray
Write-Host "   • Phase 2: Backs up BIG files (8 MB resumable chunks, up to 2 GB!)" -ForegroundColor Gray
Write-Host "   • Keeps every file AS-IT-IS in its exact folder Forum Topic" -ForegroundColor Gray
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Press ENTER to back up default folders (Desktop, Documents, Pictures, Downloads)" -ForegroundColor Yellow
Write-Host "OR paste a specific folder/drive path (e.g. D:\ or D:\Study) and press ENTER:" -ForegroundColor Yellow
$customPath = Read-Host "Folder path (Leave blank for default PC folders)"

$Roots = @()
if ($customPath -and $customPath.Trim() -ne "") {
    $cp = $customPath.Trim().Trim('"')
    if (-not (Test-Path $cp)) {
        Log "ERROR: Folder not found: $cp" 'Red'
        Read-Host 'Press Enter to close'
        exit
    }
    $folderLabel = (Split-Path $cp -Leaf).TrimEnd('\', '/')
    if (-not $folderLabel -or $cp -match '^[A-Za-z]:\\?$') {
        $driveLetter = ($cp.Substring(0, 1)).ToUpper()
        $folderLabel = "${driveLetter}:"
    }
    $Roots += @{ label = $folderLabel; path = $cp }
} else {
    $Roots = @(
        @{ label = "Desktop";   path = [Environment]::GetFolderPath('Desktop') },
        @{ label = "Documents"; path = [Environment]::GetFolderPath('MyDocuments') },
        @{ label = "Pictures";  path = [Environment]::GetFolderPath('MyPictures') },
        @{ label = "Downloads"; path = (Join-Path $env:USERPROFILE 'Downloads') }
    )
}

Send-Status "started" "Small-First Folder Backup started on $($env:COMPUTERNAME)"

$uploadedCount = 0
$skippedCount  = 0
$failedCount   = 0
$pendingQueue  = New-Object System.Collections.Generic.List[object]

foreach ($root in $Roots) {
    $basePath = $root.path
    $baseLabel = $root.label.TrimEnd('\', '/')
    if (-not $basePath -or -not (Test-Path $basePath)) {
        Log "Skipping missing folder: $baseLabel ($basePath)" 'DarkYellow'
        continue
    }

    Write-Host ""
    Log "🔍 Scanning folder tree: $baseLabel ($basePath) ..." 'Cyan'

    $allFiles = @(Get-ChildItem -Path $basePath -Recurse -File -Force -ErrorAction SilentlyContinue | Where-Object {
        $_.Length -gt 0 -and
        $_.Length -le (1980L * 1024L * 1024L) -and
        $_.Name -notmatch '^(thumbs\.db|desktop\.ini|\.~.*|~\$.*)$' -and
        $_.FullName -notmatch '\\(\$RECYCLE\.BIN|System Volume Information|\.git|node_modules|__pycache__|Temp|Cache|Code Cache|GPUCache|\.venv|venv)\\'
    })

    $totalInRoot = $allFiles.Count
    if ($totalInRoot -eq 0) {
        Log "  -> No files found in $baseLabel." 'DarkGray'
        continue
    }
    Log "  -> Found $totalInRoot file(s) in $baseLabel. Checking already-synced files by folder..." 'Green'

    $basePrefix = $basePath.TrimEnd('\', '/')
    $grouped = $allFiles | Group-Object {
        $dir = $_.DirectoryName.TrimEnd('\', '/')
        if ($dir.Length -le $basePrefix.Length) {
            return $baseLabel
        } else {
            $relSub = $dir.Substring($basePrefix.Length).TrimStart('\', '/').Replace('\', '/')
            return "$baseLabel/$relSub"
        }
    }

    $folderIdx = 0
    $folderTotal = $grouped.Count
    foreach ($grp in $grouped) {
        $folderIdx++
        $topicFolder = $grp.Name
        $folderFiles = @($grp.Group)
        Write-Host "`r  -> Checking Storage index: folder $folderIdx / $folderTotal ($topicFolder)...                     " -NoNewline -ForegroundColor Gray
        $existingMap = Get-ExistingMap $topicFolder $folderFiles

        foreach ($f in $folderFiles) {
            if ($existingMap -and $existingMap.ContainsKey($f.Name.ToLower())) {
                $skippedCount++
            } else {
                $pendingQueue.Add([PSCustomObject]@{
                    File   = $f
                    Folder = $topicFolder
                    Size   = [int64]$f.Length
                })
            }
        }
    }
    Write-Host ""
}

$toUploadTotal = $pendingQueue.Count
Write-Host ""
Write-Host "======================================================================" -ForegroundColor Cyan
Log "📊 SCAN SUMMARY:" 'Cyan'
Log "  • Already in Storage (Skipped) : $skippedCount file(s)" 'Green'
Log "  • Remaining to Upload          : $toUploadTotal file(s)" 'Yellow'
Write-Host "======================================================================" -ForegroundColor Cyan

if ($toUploadTotal -gt 0) {
    Log "⚡ Sorting $toUploadTotal remaining file(s): SMALLEST files first -> BIGGEST files last (keeping exact folders)..." 'Cyan'
    $sortedQueue = @($pendingQueue | Sort-Object Size)

    $smallCount = @($sortedQueue | Where-Object { $_.Size -le (4 * 1024 * 1024) }).Count
    $bigCount   = $toUploadTotal - $smallCount
    Log "  -> Phase 1 (Small Files <= 4 MB) : $smallCount file(s)" 'Green'
    Log "  -> Phase 2 (Big Files > 4 MB)    : $bigCount file(s) (8 MB Chunked Upload)" 'Magenta'
    Write-Host ""

    $curIdx = 0
    foreach ($item in $sortedQueue) {
        $curIdx++
        $st = Upload-SingleFile $item.File $item.Folder $curIdx $toUploadTotal
        if ($st -eq 'uploaded') { $uploadedCount++ }
        elseif ($st -eq 'skipped') { $skippedCount++ }
        else { $failedCount++ }
    }
}

Send-Status "done" "Sync Complete on $($env:COMPUTERNAME): $uploadedCount uploaded, $skippedCount already in storage, $failedCount failed"

Write-Host ""
Write-Host "======================================================================" -ForegroundColor Green
Log "SYNC COMPLETE!" 'Green'
Log "  • Newly Uploaded : $uploadedCount file(s)" 'Green'
Log "  • Already Synced : $skippedCount file(s) (Skipped)" 'Cyan'
Log "  • Failed/Locked  : $failedCount file(s)" $(if ($failedCount -gt 0) { 'Yellow' } else { 'Gray' })
Write-Host ""
Log "Open $Base/browse/ to browse your folders and View/Download any file directly!" 'Cyan'
Write-Host "======================================================================" -ForegroundColor Green
$Host.UI.RawUI.WindowTitle = "TG Storage Sync - DONE ($uploadedCount uploaded, $skippedCount skipped)"
Read-Host 'Press Enter to close this window'
