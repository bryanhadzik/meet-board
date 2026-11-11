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
    # 1) Newer builds carry the version in the exe's file properties: instant.
    try {
        $pv = (Get-Item $Exe).VersionInfo.ProductVersion
        if ($pv -and $pv -match '\d+\.\d+') { return $pv.Trim() }
    } catch { }
    # 2) Older builds: ask the exe. Run it as a plain process and read stdout,
    #    so warnings on stderr can't turn into a PowerShell error.
    try {
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $Exe
        $psi.Arguments = '--version'
        $psi.UseShellExecute = $false
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        $psi.CreateNoWindow = $true
        $p = [System.Diagnostics.Process]::Start($psi)
        $out = $p.StandardOutput.ReadToEnd()
        [void]$p.StandardError.ReadToEnd()
        if (-not $p.WaitForExit(30000)) { try { $p.Kill() } catch { } }
        $line = ($out -split "`r?`n" | Where-Object { $_ -match '^\s*v?\d+\.\d+' } | Select-Object -Last 1)
        if ($line) { return $line.Trim() }
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
        Update-LauncherScript

        if ($wasRunning) { Start-Server }
    } catch {
        Log ('Update failed: ' + $_.Exception.Message)
    } finally {
        Set-Busy $false
    }
}

function Update-LauncherScript {
    # The launcher ships with each release too; refresh this script file so the
    # next time it opens it's current. (The running window keeps the old code.)
    try {
        $asset = $script:Latest.assets | Where-Object { $_.name -eq 'meet-board-launcher.ps1' } | Select-Object -First 1
        if (-not $asset) { return }
        $self = Join-Path $AppDir 'meet-board-launcher.ps1'
        $tmp = $self + '.new'
        Save-ReleaseAsset $asset $tmp
        $new = [System.IO.File]::ReadAllBytes($tmp)
        $old = @(); if (Test-Path $self) { $old = [System.IO.File]::ReadAllBytes($self) }
        if ($new.Length -lt 1000) { Remove-Item $tmp -Force; return }
        if ([Convert]::ToBase64String($new) -ne [Convert]::ToBase64String($old)) {
            Move-Item -Path $tmp -Destination $self -Force
            Unblock-File -Path $self -ErrorAction SilentlyContinue
            Log 'Launcher updated too - close and reopen it to use the new version.'
        } else {
            Remove-Item $tmp -Force
        }
    } catch {
        Log ('Could not update the launcher itself: ' + $_.Exception.Message)
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
    $linkMusic.Enabled = $running

    $verText = 'Installed: '
    if ($installed) { $verText += $installed } else { $verText += '(none)' }
    if ($script:Latest) {
        $lv = $script:Latest.tag_name.TrimStart('v')
        $verText += "     Latest: $lv"
        if (-not $installed -or $installed -eq 'unknown' -or (Compare-Version $lv $installed) -gt 0) {
            $btnUpdate.Text = $(if ($installed) { "Update to $lv" } else { "Install $lv" })
            $btnUpdate.BackColor = [System.Drawing.Color]::FromArgb(34, 122, 69)
            $btnUpdate.ForeColor = [System.Drawing.Color]::White
        } else {
            $btnUpdate.Text = "Up to date ($lv)"
            $btnUpdate.BackColor = [System.Drawing.SystemColors]::Control
            $btnUpdate.ForeColor = [System.Drawing.SystemColors]::ControlText
        }
    } else {
        $btnUpdate.Text = $(if ($installed) { 'Update' } else { 'Install' })
    }
    $lblVersion.Text = $verText
}

# ---------------------------------------------------------------- UI

# ---------------------------------------------------------------- icon
# The meet-board icon is embedded so the launcher never shows PowerShell's.
$IconBase64 = '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'
$IconFile = Join-Path $AppDir 'meet-board.ico'
try {
    $iconBytes = [Convert]::FromBase64String($IconBase64)
    # rewrite when the embedded icon changes (an update brings a new one)
    $same = $false
    if (Test-Path $IconFile) { try { $same = ([Convert]::ToBase64String([System.IO.File]::ReadAllBytes($IconFile)) -eq $IconBase64) } catch { } }
    if (-not $same) { [System.IO.File]::WriteAllBytes($IconFile, $iconBytes) }
    $AppIcon = New-Object System.Drawing.Icon (New-Object System.IO.MemoryStream (, $iconBytes))
} catch { $AppIcon = $null }

# Own taskbar identity, so the window isn't grouped under PowerShell
try {
    Add-Type -Namespace MeetBoard -Name Shell -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("shell32.dll", SetLastError = true)]
public static extern int SetCurrentProcessExplicitAppUserModelID([System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.LPWStr)] string AppID);
'@
    [void][MeetBoard.Shell]::SetCurrentProcessExplicitAppUserModelID('bryanhadzik.meet-board.launcher')
} catch { }

function New-DesktopShortcut {
    try {
        $desktop = [Environment]::GetFolderPath('Desktop')
        $lnkPath = Join-Path $desktop 'meet-board.lnk'
        $ps1 = Join-Path $AppDir 'meet-board-launcher.ps1'
        $sh = New-Object -ComObject WScript.Shell
        $lnk = $sh.CreateShortcut($lnkPath)
        $lnk.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $lnk.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $ps1 + '"'
        $lnk.WorkingDirectory = $AppDir
        $lnk.IconLocation = $IconFile + ',0'
        $lnk.Description = 'meet-board launcher'
        $lnk.Save()
        Log "Desktop shortcut created: $lnkPath"
    } catch {
        Log ('Could not create shortcut: ' + $_.Exception.Message)
    }
}

$form = New-Object System.Windows.Forms.Form
if ($AppIcon) { $form.Icon = $AppIcon }
$form.Text = 'meet-board'
$form.Size = New-Object System.Drawing.Size(560, 470)
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

$linkMusic = New-Object System.Windows.Forms.LinkLabel
$linkMusic.Text = 'Music Speaker'
$linkMusic.Location = New-Object System.Drawing.Point(18, 400)
$linkMusic.AutoSize = $true
$form.Controls.Add($linkMusic)

$linkShortcut = New-Object System.Windows.Forms.LinkLabel
$linkShortcut.Text = 'Desktop Shortcut'
$linkShortcut.Location = New-Object System.Drawing.Point(390, 132)
$linkShortcut.AutoSize = $true
$form.Controls.Add($linkShortcut)

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
$linkShortcut.Add_LinkClicked({ New-DesktopShortcut })
# The tab that actually plays meet music through this PC's speakers
$linkMusic.Add_LinkClicked({ Start-Process ('http://localhost:' + (Get-Port) + '/music?speaker=1') })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 3000
$timer.Add_Tick({ Update-Status })

$form.Add_Shown({
    Log "Folder: $AppDir"
    # a desktop shortcut with the meet-board icon (not PowerShell's); re-saved so a new icon shows
    if (-not (Test-Path (Join-Path ([Environment]::GetFolderPath('Desktop')) 'meet-board.lnk'))) { New-DesktopShortcut }
    $script:InstalledVersion = Get-InstalledVersion
    Update-Status
    Log 'Checking GitHub for the latest release...'
    Check-Latest
    if ($script:Latest) { Log ('Latest release: ' + $script:Latest.tag_name) }
    $timer.Start()
})
$form.Add_FormClosing({ $timer.Stop() })   # closing the launcher leaves the server running

[void]$form.ShowDialog()
