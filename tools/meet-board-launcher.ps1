<#
  meet-board launcher: small Windows UI to start/stop the server and update
  meet-board.exe from GitHub Releases.

  Put this file (and meet-board-launcher.cmd) in the same folder as
  meet-board.exe, e.g. C:\MeetBoard, and double-click the .cmd.
  If meet-board.exe isn't there yet, "Install" downloads it.

  Works with Windows PowerShell 5.1 (built into Windows 10/11).
#>

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$Repo     = 'bryanhadzik/meet-board'
$AppDir   = $PSScriptRoot
if (-not $AppDir) { $AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$Exe      = Join-Path $AppDir 'meet-board.exe'
$Settings = Join-Path $AppDir 'settings.json'

$script:Latest = $null   # last release info from GitHub

# ---------------------------------------------------------------- helpers

function Get-Port {
    try {
        $s = Get-Content $Settings -Raw | ConvertFrom-Json
        if ($s.http_port) { return [int]$s.http_port }
    } catch { }
    return 5000
}

function Get-ServerProcesses {
    # One-file PyInstaller exes run as two processes with the same path.
    @(Get-Process -Name 'meet-board' -ErrorAction SilentlyContinue |
        Where-Object { try { $_.Path -eq $Exe } catch { $false } })
}

function Get-InstalledVersion {
    if (-not (Test-Path $Exe)) { return $null }
    try {
        $out = & $Exe --version 2>$null | Select-Object -Last 1
        if ($out) { return $out.Trim() }
    } catch { }
    return 'unknown'
}

function Get-GitHubToken {
    # Needed only while the repo is private. Order: GITHUB_TOKEN, settings.json
    # github_token, then the GitHub CLI login (gh auth login) if gh is installed.
    if ($env:GITHUB_TOKEN) { return $env:GITHUB_TOKEN }
    try {
        $s = Get-Content $Settings -Raw -ErrorAction Stop | ConvertFrom-Json
        if ($s.github_token) { return [string]$s.github_token }
    } catch { }
    if (Get-Command gh -ErrorAction SilentlyContinue) {
        try {
            $t = (& gh auth token 2>$null | Select-Object -First 1)
            if ($t) { return $t.Trim() }
        } catch { }
    }
    return $null
}

function Get-ApiHeaders {
    $h = @{ 'User-Agent' = 'meet-board-launcher'; 'Accept' = 'application/vnd.github+json' }
    $tok = Get-GitHubToken
    if ($tok) { $h['Authorization'] = "Bearer $tok" }
    return $h
}

function Save-ReleaseAsset($asset, [string]$outFile) {
    $tok = Get-GitHubToken
    if (-not $tok) {
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $outFile -UseBasicParsing `
            -Headers @{ 'User-Agent' = 'meet-board-launcher' }
        return
    }
    # Private repo: ask the API for the asset, then follow the redirect to the
    # signed download URL WITHOUT the token (storage rejects it).
    $req = [System.Net.HttpWebRequest]::Create($asset.url)
    $req.Headers.Add('Authorization', "Bearer $tok")
    $req.Accept = 'application/octet-stream'
    $req.UserAgent = 'meet-board-launcher'
    $req.AllowAutoRedirect = $false
    $resp = $req.GetResponse()
    $loc = $resp.Headers['Location']
    $resp.Close()
    if (-not $loc) { throw 'GitHub did not return a download location.' }
    Invoke-WebRequest -Uri $loc -OutFile $outFile -UseBasicParsing -Headers @{ 'User-Agent' = 'meet-board-launcher' }
}

function Compare-Version([string]$a, [string]$b) {
    # Returns 1 if a > b, -1 if a < b, 0 if equal. Non-numeric parts ignored.
    $pa = @([regex]::Matches($a, '\d+') | ForEach-Object { [int]$_.Value })
    $pb = @([regex]::Matches($b, '\d+') | ForEach-Object { [int]$_.Value })
    for ($i = 0; $i -lt [Math]::Max($pa.Count, $pb.Count); $i++) {
        $x = 0; $y = 0
        if ($i -lt $pa.Count) { $x = $pa[$i] }
        if ($i -lt $pb.Count) { $y = $pb[$i] }
        if ($x -gt $y) { return 1 }
        if ($x -lt $y) { return -1 }
    }
    return 0
}

function Log([string]$msg) {
    $logBox.AppendText((Get-Date -Format 'HH:mm:ss') + '  ' + $msg + "`r`n")
    [System.Windows.Forms.Application]::DoEvents()
}

function Set-Busy([bool]$busy) {
    $form.UseWaitCursor = $busy
    foreach ($b in @($btnStart, $btnStop, $btnUpdate, $btnCheck)) { $b.Enabled = -not $busy }
    [System.Windows.Forms.Application]::DoEvents()
    if (-not $busy) { Update-Status }
}

# ---------------------------------------------------------------- actions

function Start-Server {
    if (-not (Test-Path $Exe)) { Log 'meet-board.exe is not installed yet - click Install.'; return }
    if ((Get-ServerProcesses).Count -gt 0) { Log 'Server is already running.'; return }
    Start-Process -FilePath $Exe -WorkingDirectory $AppDir -WindowStyle Minimized | Out-Null
    Log ('Server started. Board: http://' + $env:COMPUTERNAME + ':' + (Get-Port) + '/web/meetboard')
    Start-Sleep -Milliseconds 800
    Update-Status
}

function Stop-Server {
    $procs = Get-ServerProcesses
    if ($procs.Count -eq 0) { Log 'Server is not running.'; return }
    $procs | ForEach-Object { try { Stop-Process -Id $_.Id -Force } catch { } }
    for ($i = 0; $i -lt 20 -and (Get-ServerProcesses).Count -gt 0; $i++) { Start-Sleep -Milliseconds 250 }
    Log 'Server stopped.'
    Update-Status
}

function Check-Latest {
    try {
        $script:Latest = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" `
            -Headers (Get-ApiHeaders) -TimeoutSec 20
        $asset = $script:Latest.assets | Where-Object { $_.name -eq 'meet-board.exe' } | Select-Object -First 1
        if (-not $asset) { Log 'Latest release has no meet-board.exe attached.'; $script:Latest = $null }
    } catch {
        $script:Latest = $null
        $code = $null
        try { $code = [int]$_.Exception.Response.StatusCode } catch { }
        if ($code -eq 404) {
            if (Get-GitHubToken) {
                Log "No release published yet for $Repo (the first GitHub Actions build creates one)."
            } else {
                Log "No release visible for $Repo. If the repo is private, sign in with 'gh auth login' or add github_token to settings.json."
            }
        } else {
            Log ('Could not reach GitHub: ' + $_.Exception.Message)
        }
    }
    Update-Status
}

function Update-App {
    Set-Busy $true
    try {
        if (-not $script:Latest) { Check-Latest }
        if (-not $script:Latest) { return }
        $latestVer = $script:Latest.tag_name.TrimStart('v')
        $installed = Get-InstalledVersion
        if ($installed -and $installed -ne 'unknown' -and (Compare-Version $latestVer $installed) -le 0) {
            Log "Already on the latest version ($installed)."
            return
        }
        $asset = $script:Latest.assets | Where-Object { $_.name -eq 'meet-board.exe' } | Select-Object -First 1
        $tmp = $Exe + '.new'
        Log "Downloading meet-board $latestVer ..."
        Save-ReleaseAsset $asset $tmp

        # Sanity check: a real Windows exe starts with "MZ"
        $head = [System.IO.File]::ReadAllBytes($tmp)[0..1]
        if ((Get-Item $tmp).Length -lt 1MB -or $head[0] -ne 0x4D -or $head[1] -ne 0x5A) {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            throw 'Downloaded file is not a valid exe.'
        }

        $wasRunning = (Get-ServerProcesses).Count -gt 0
        if ($wasRunning) { Log 'Stopping server for the update...'; Stop-Server }

        $moved = $false
        for ($i = 0; $i -lt 20 -and -not $moved; $i++) {
            try { Move-Item -Path $tmp -Destination $Exe -Force; $moved = $true }
            catch { Start-Sleep -Milliseconds 500 }   # file can stay locked briefly after exit
        }
        if (-not $moved) { throw 'Could not replace meet-board.exe (still in use?).' }
        Unblock-File -Path $Exe -ErrorAction SilentlyContinue
        Log "Installed meet-board $latestVer."

        if ($wasRunning) { Start-Server }
    } catch {
        Log ('Update failed: ' + $_.Exception.Message)
    } finally {
        Set-Busy $false
    }
}

function Update-Status {
    $procs = Get-ServerProcesses
    $running = $procs.Count -gt 0
    $installed = $script:InstalledVersion
    if ($running) {
        $lblStatus.Text = 'RUNNING on port ' + (Get-Port)
        $lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(34, 122, 69)
    } elseif (-not (Test-Path $Exe)) {
        $lblStatus.Text = 'NOT INSTALLED'
        $lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(163, 40, 45)
    } else {
        $lblStatus.Text = 'STOPPED'
        $lblStatus.ForeColor = [System.Drawing.Color]::FromArgb(163, 40, 45)
    }
    $btnStart.Enabled = (-not $running) -and (Test-Path $Exe)
    $btnStop.Enabled  = $running
    $linkSettings.Enabled = $running
    $linkBoard.Enabled = $running

    $verText = 'Installed: '
    if ($installed) { $verText += $installed } else { $verText += '(none)' }
    if ($script:Latest) {
        $lv = $script:Latest.tag_name.TrimStart('v')
        $verText += "     Latest: $lv"
        if (-not $installed -or ($installed -ne 'unknown' -and (Compare-Version $lv $installed) -gt 0)) {
            $btnUpdate.Text = $(if ($installed) { "Update to $lv" } else { "Install $lv" })
            $btnUpdate.BackColor = [System.Drawing.Color]::FromArgb(34, 122, 69)
            $btnUpdate.ForeColor = [System.Drawing.Color]::White
        } else {
            $btnUpdate.Text = 'Up to date'
            $btnUpdate.BackColor = [System.Drawing.SystemColors]::Control
            $btnUpdate.ForeColor = [System.Drawing.SystemColors]::ControlText
        }
    } else {
        $btnUpdate.Text = $(if ($installed) { 'Update' } else { 'Install' })
    }
    $lblVersion.Text = $verText
}

# ---------------------------------------------------------------- UI

$form = New-Object System.Windows.Forms.Form
$form.Text = 'meet-board'
$form.Size = New-Object System.Drawing.Size(560, 430)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox = $false
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)

$lblTitle = New-Object System.Windows.Forms.Label
$lblTitle.Text = 'meet-board'
$lblTitle.Font = New-Object System.Drawing.Font('Segoe UI', 16, [System.Drawing.FontStyle]::Bold)
$lblTitle.Location = New-Object System.Drawing.Point(16, 12)
$lblTitle.AutoSize = $true
$form.Controls.Add($lblTitle)

$lblStatus = New-Object System.Windows.Forms.Label
$lblStatus.Font = New-Object System.Drawing.Font('Segoe UI', 11, [System.Drawing.FontStyle]::Bold)
$lblStatus.Location = New-Object System.Drawing.Point(200, 20)
$lblStatus.Size = New-Object System.Drawing.Size(330, 24)
$lblStatus.TextAlign = 'MiddleRight'
$form.Controls.Add($lblStatus)

$lblVersion = New-Object System.Windows.Forms.Label
$lblVersion.Location = New-Object System.Drawing.Point(18, 52)
$lblVersion.Size = New-Object System.Drawing.Size(510, 22)
$lblVersion.ForeColor = [System.Drawing.Color]::DimGray
$form.Controls.Add($lblVersion)

function New-Button($text, $x) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text
    $b.Location = New-Object System.Drawing.Point($x, 84)
    $b.Size = New-Object System.Drawing.Size(120, 36)
    $form.Controls.Add($b)
    return $b
}
$btnStart  = New-Button 'Start'  16
$btnStop   = New-Button 'Stop'   144
$btnUpdate = New-Button 'Update' 272
$btnCheck  = New-Button 'Check GitHub' 400
$btnUpdate.FlatStyle = 'Flat'

$linkSettings = New-Object System.Windows.Forms.LinkLabel
$linkSettings.Text = 'Open Settings'
$linkSettings.Location = New-Object System.Drawing.Point(18, 132)
$linkSettings.AutoSize = $true
$form.Controls.Add($linkSettings)

$linkBoard = New-Object System.Windows.Forms.LinkLabel
$linkBoard.Text = 'Open Meet Board'
$linkBoard.Location = New-Object System.Drawing.Point(140, 132)
$linkBoard.AutoSize = $true
$form.Controls.Add($linkBoard)

$linkFolder = New-Object System.Windows.Forms.LinkLabel
$linkFolder.Text = 'Open Folder'
$linkFolder.Location = New-Object System.Drawing.Point(280, 132)
$linkFolder.AutoSize = $true
$form.Controls.Add($linkFolder)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Multiline = $true
$logBox.ReadOnly = $true
$logBox.ScrollBars = 'Vertical'
$logBox.Font = New-Object System.Drawing.Font('Consolas', 9)
$logBox.Location = New-Object System.Drawing.Point(16, 162)
$logBox.Size = New-Object System.Drawing.Size(512, 212)
$form.Controls.Add($logBox)

# ---------------------------------------------------------------- wiring

$btnStart.Add_Click({ Set-Busy $true; try { Start-Server } finally { Set-Busy $false } })
$btnStop.Add_Click({ Set-Busy $true; try { Stop-Server } finally { Set-Busy $false } })
$btnUpdate.Add_Click({
    Update-App
    $script:InstalledVersion = Get-InstalledVersion
    Update-Status
})
$btnCheck.Add_Click({ Set-Busy $true; try { Check-Latest } finally { Set-Busy $false } })
$linkSettings.Add_LinkClicked({ Start-Process ('http://localhost:' + (Get-Port) + '/settings') })
$linkBoard.Add_LinkClicked({ Start-Process ('http://localhost:' + (Get-Port) + '/web/meetboard') })
$linkFolder.Add_LinkClicked({ Start-Process explorer.exe $AppDir })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 3000
$timer.Add_Tick({ Update-Status })

$form.Add_Shown({
    Log "Folder: $AppDir"
    $script:InstalledVersion = Get-InstalledVersion
    Update-Status
    Log 'Checking GitHub for the latest release...'
    Check-Latest
    if ($script:Latest) { Log ('Latest release: ' + $script:Latest.tag_name) }
    $timer.Start()
})
$form.Add_FormClosing({ $timer.Stop() })   # closing the launcher leaves the server running

[void]$form.ShowDialog()
