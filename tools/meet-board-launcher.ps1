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
$IconBase64 = 'AAABAAcAEBAAAAAAIAAmAwAAdgAAABgYAAAAACAAaAUAAJwDAAAgIAAAAAAgALYGAAAECQAAMDAAAAAAIAB/CgAAug8AAEBAAAAAACAAAQ4AADkaAACAgAAAAAAgAG8YAAA6KAAAAAAAAAAAIABOLAAAqUAAAIlQTkcNChoKAAAADUlIRFIAAAAQAAAAEAgGAAAAH/P/YQAAAu1JREFUeJxVkk1oXGUUhp/zfd/N/ZlkJqEtilgLKqgIblQEIUW6EmqsC5VudOGiRWvVdOGuiFhEBJeCdaNrJVpiRRQrpv5QF9riT9DGWjVgGzomk5nMnTv3u/c7LiY1+mwO53B433fxCgCzs+mU2b7Pok3vPUZUwFB4TxJZDAZPoKpUsQaxttvv1/O8+WIuHH6hmYX6ZI6ZZjiEyIECqjSylP6ggLoGZ2kkMRoUcQ4tBl9Hzu51U8IDXbHTs9N3VjN33GbmvvtJm2nMta0Jjrz9Lkcf3sueW27k/bOLvH7qC7I4ltqXgTS71+eDB11dFi2RKKxuFPJbe820+32KyhOCItay0u1x4coq7Y0+IgYVAVCqOqgvWpIdOnqAOD6ed1ZraqykCRoUNNAcb9Dt51DVEDkmkpiqrhGoiWKr3h9y+smcWOvYlU2ylnforncAAYFuCGDMaNdAzxjGJya4ioZa3GBpkT13zfDyzCsc/vRZ7n98P0YsqIJsPqrinOOvS5eZm/8AayyD3jplkeNMGpPXAy51l+kPhywuXRgJoP86BQ2MNxp0exuoGIII6hxEMc4AS2XFS4XQGw5ZXv4TEbsVc9P94u9/0F79m8nWJBoCBIUQcGE4JGpdT7FzN9t3XMNj+/ehbCUIGsjSlBMffsRXZ77BOgeAGDOabmrq4A2tHW88eut99VvnT9tO3sOK4b+oKmmakMQJvvKEEOpyMLBV5Z92lS+4uzHBMVtz5uabOPjMU9igqPxfoCw9WZbyznsn+PjUZ7ixiKocqjMmMefyjh6PrnD+l1954sCTOCMoIAhBA/HYGI2sgaL40mMBX3rFGOtCd/DDrumW3HPkOrvzWF4/99AjGFFQCJvRvz17jrn5k6RJAsYwliSm9qXooPjeQfHlVBa9+vOPnefX19bt5wunMVZQHYk457i8skJkR91QVWoNqOprRae9IKOaoTC+2zWj26vu2tXbFsbR2LaNQFCwqlotFu32AiD/AE6IdWwpcz6KAAAAAElFTkSuQmCCiVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAFL0lEQVR4nI2VbYhdVxWGn7XPOfec+zUzmeCYTEyatFRN7LSaNm2FWBEsldZqQYSo+MMP/BEEUZBSmzJJSxDUCFqCJSoIQrEFQfyhVK1RNJJOY5u2aSeThqQzzddkmpk7zp3zvffqj3tnMreZqi9sOPd8vGu/633XvsJKfGO0FjX8tSQp2FKI+P+QAp6vVCNSac1y4MDi0iNhdNSwb5+r7X54jwTBVz3VQVU1ANY5PGNYulbAiGBElrlL57j6C8XzZ3Hlr+LHH3uUvXs7b9Z2P/w90zewP51vUaYZCCBCtRqRJCkoVGsRRgxJnuOKokNnDLUoYrmCKogg9SZuobUvObh/r7B7tFEXd6pQHfrA0CCf/fCHjBFhZqHNk0df4EsfvZXI9/nlkedJ0oztmzdy97YbERGmZls8NXYc6arsVnF4Plra2ahZ3OhH5dygqfQ38jj1Prd9REfv/+Sy4iOnJzn4xQcA+OOJU4zPTvK1j93O13fu6LS+KPnd8VdJixLPCKoAYnBOBFePL2drJfr8lzd5aze8Yo3fN1gN9bbN7xMjwlycMHZmijtuuI7AMxw5PYlzjnX9TW7ZOIwA0/9p8+LUeTxj0JU+GCOUxaK9PH+zRA+ObjKpOSGe17Sl1TTLOgqMUAtD4iwDhVpYQUTIraXIV3gQVmAlPXSMQGNLZcTnzBmQCiqCLTNqxsf3fACcKn0iiIB1HZJIhFo3RaqKU0W7axliQB1Yxeeff4GsAJTr+odZLGLm4tbVXf4PqCq+7xFWwu72FXWukyi/xB+uDnM2nuCem+7j0BcO8f3DP+Tpk7/l1q23UNoSVqb8Wno8z2Ou1WJy6k2M53XMby9QpAkRdfwoArUlG9dsZP1AH7ZUbtr6QX724x+xEC/2DFUPBJx1NBp1/vGvo3znoT1Evg8iGGO6MwH+XJrg+SEvTR3jby8fJk6vMHb833zi3k/j1PXa18uPKqg6ytLiCRR5jgB2aRBJ8eeSlIoxnIwX+NbcNHFeYrKU8+2Ly21YvfcQ+D61Wg0REBHQztuqrhMkFL+6Zj3x1Dne8/572PLxXYzPnOXOHdt55JH9pMli58N3wDlHs9HgmWf/yk+fOES9Vsc521EmgrW2o0IiOnkUQ560aKVgjXD50gx/eOZPlEW2egFVwkqF8YlTBL7fUdC9393B1VauWX/9prRsnwgr9ebOkfv13JXX5cTZY5Sz88syV3VAHYQhQb2Oc44gCKhVq6iqFmkq6mxcio7466sw3ip5YGCQJ5sZD83GXNpyA/fuup3cWv5bTFUd1jqCwOfc+Qu8/OprVIIAdY48iYmWWqTOMVytw8A6dPI0129ex7e/uZuFJMGIedcCANZZmo0Gfz78d1546RWiarXndPVnYydBEPDs9EV+P/EaF/KU5597jp13f4rOX8nqKRKk93gAwrBCFi9S5nmni6pizEDUJicv1onGX/FJtxlMDmJkOX7XLAQxQhSFhGF3ReHK6gpSWMna/vSpk1csfUfv+siG+3Z9Zlt+5Njl4LYdd/DEgUdpLyYY0+uBtY6+ZpODP/8Fv/7N0/T19eGsvcptTOGcq5R5NhbPzEz7pUVEkgffmovvSufTpu9XOPvGG/zgJ49TFJZ3QlUJgoDxiQnCMMTZ3mkXqFhbLpYl36WbQwO4DVs23DyydWj/61MLd755sV3N86R7Jl/TfFAlDCPCKFzpg4pIgshYmZZ74rcuvAgs6zfA0nS8t39oqN7f37+quUuYn58nzbKee1lRxMTxpZWcbwN5jHVB6Z/kkAAAAABJRU5ErkJggolQTkcNChoKAAAADUlIRFIAAAAgAAAAIAgGAAAAc3p69AAABn1JREFUeJy9l22MnFUVx3/n3udlZnZ2S60FAwkkRDQIhQSjnzSkJEYbtCTG1fULaWzRImg0Agla02ygSOATxEQxUaMG/YCaaqS0FUGaoglEkzZbSpvaV7ul7W5nZ+f1mee59/jhmZnd6c6yJVH/n+aee+49//P63IEexsct/y8ssiUAbN9umJz05fsfuVmD0iaPvwXvQ1Tlv2JQRBGTGWPeopP8ovHjH0z1bArj45YXXnCFrY9OmLjwcwnDglUdOO+8B8Aak6/VwyKVnhxyse/qL4GxaJZ26CT3NX/0xC8ZH7cCMLp124ddaKZAAnVZ2ko6hkUkioUYgFY7AaBQiDGSB8er0u7Ku95SKkT0gjsI9RgbgvEienvj2cmpAMAZ/1WJS4FvNlIRCTfcdjNjPaNpyitv/wtVZeMd6wis4ZXDx0iyDIA4sHz6o+uIgwABqq02rx09jgxNnlicS6VYCLXVuB94IABQY24JVLWRJOaeO9ax8+v3Dhz74nPP004z/vBALn/kt7t4evdfAdjyyU/w7MTGAf0Nz/yM3VNvM1os9tO3KEIG7xRjPgIQdGOLxkUxxjBbbzJTbxAHQT+flxpNkixjrtnCGsN0dR5jBBDeqdaottqICAK004xKV08vq6VuGvI8tVs5H4DifQ/vNeWxT9FJXMc5e83YKMUw59ZxjnNzNRTl2qvGsGI4U5kjtHknpc5x3VWrCG1eiM1OyoVanSgIliGAkyi22qztaz731J25lTAC9agqkTGcr873D4sIkbWAMF2poprnvbcfGcPZylx/bbr6ulwnAKiHIGIhBVEIcTEPiBgKQv9CZeF31K2sxZ5pVy6L1st4voC40At+TkD27UGCEJwjcQkoRF2GwtKGGtpgQ/aH0RBAbIC6bIEAxw+DGLx33Lj6eowxTM+fBhRV0KFXrQwrwmAwFO8ceY/qAoGwVGa+XWPiY1/m8Y07OFU5zebfb+VSvUJkAjAy3J3l0L0/cxlxFC2IRWg36qTt9iABPKjLuGntBwlNgKDU5ue55+7P8Pi27zJfqw+M25XgvGdstMwfd+3m0cnHKI+M9MezEZMX4QABAFUQoVwYoxCNEEUxZ85Os+cvr5IkCTJ8tA2FV6VYKHBg6hBBEACSzwmRJQUU5Ac8YVhg76EXufV91zPbmsOI540332T//tffk3FVxfu8pcMwZHS0TJq06FnOkvZSAs57YhNwsDLNQ/8+gvGOZifBotghrN/FOjYMKBWLoIpXxaXpYHScQ0T6rRoAGBuQtWa5Yf33uGHDN2lfqHLk+J9Ye/Uabrt1Hd77FTmoKkEQcHFmlqnDhzHGYC8r3DwNhqRZH4xAdxsJIjr1BNeapVar8aXPfZ5nnthBrd3GrFCEXj2luMCBQ29x9/hE39OB9hnyvgkWL3yrii3FdBqrGRsbY+euXfzj4CFYabLlt2OspVqdx3uPsbZ7bsHo4tAPEPAuI4hHOfXGT3D1d7B4klqFluswe37mPQ0iay1RFOEWGfLe5zeoknaSpTUQGqh75bPlMk+bs5xL29wbR8w0MxwOkZVnQM/X1Hs6zdaAfLRc7qdQvSdLO4MREAzOZ9y15jpuuvpGolaNzsmj3LX+Tr6xZTPNVgsx5oqbAfJZEIchZ85OM/nkU6RpirUWF8VLCfQgKOAAl3+aw4hVY6PYIOi/Aa8UqkoURZTLI4Nz5LJsBj2ZFWF/5QIbLlxiutOiEMfs/vPL7H5pD2Lkiqpg2GfYe095ZARjDC7LcFlnYD8AyNS52Fhemj9HWI4xKtRP1AlUcTjUrWxcgCgKkcsTJZask+RuKmT9IsT3CUSBPVapdvRbm27Xx779cY6emmf/dxo0O3DtNe9f8f+JSO7phYszeK/LvIhzRTFGO62minCsT6BYDH5TmUseXLM61not1WbDSXW+xsT4BD98cgfVRuNdB5FXz0ipxKatD7LzxV2sGhsb+udEUBUx3YbRX/cI2PMnTv8NM/rTTsbmD6wtZJVah3J5RF57/e987aGHcZlfiPPlWDRr/nngICOlUvfdu0RZRQSxJvTePd+aufgqYAPAb/s+ZnKytnXvvpNSiu1Xmm1HmqScOnmCo0cOL+v5wO0KIyMlgiDAZ+kQjXwsp+3Wr1ozF7cABvAysJtjPeGqLxRXRR8yYkJrrFzpHHQ+Q/3gZQKqIqngjznvfpdcuvTyEJu5YPv27caa/AX2P4RhUTL/A3CaKAOVJJyVAAAAAElFTkSuQmCCiVBORw0KGgoAAAANSUhEUgAAADAAAAAwCAYAAABXAvmHAAAKRklEQVR4nMWaa5BlVXXHf2vvc899N8y0DqMzQYUPkxrKTESiiNaMiJYQo8ZYGClTCKkYQaiMyRcpJWkbK0j5wbyU0hCCpTwiRkipqfEVtEgGiDMIYhwYIQETYLphumGm7z33PPbeKx/O7de9d4bumYb5V+3qe8/Zd+/133ut/1pnn4ZRuPB2y8SEGXnvRGBiwrBjIhp1S4a+q4KIArSvuHYc69bjveD9YN8XF9YqgFaZ7Xx+8iAAqoIIgC4avAiZv1G/8i8uFJGPonomGk5mmOhLBUXkEMb8TFRv6P7dNbcM2ioLFyYmBIgaB8M/SrX2IYJHnQMNJ8LwRYhBogisRbPsm0kuF/MKUiY/rSBa+vnEhDA5GerPupuk2fyQZkmhRebRoAJDjYF2pOuj+hxtnJFNg2qRe02TQuqN9zesu5XJSWXi0wLlykdMTrr65Vf/nmnUv6lpUoBUAIwIhfdDi1KxlqClGy7tE1lbzqmLfctZBDc0jlCxZmGcFUE1l0Yz1qR7cXL9X36NiYkoAkJ/p3YSQkAx9Jenm+eMN5sYWXA5FJjpJMSRRYCkcIy3Gggw2+1hjRAZg/aN9yFQhMB4s7HM+KCBmU5CPa6snICIVecCsBO4GQgC0L7iqnGv9nGMbaNBjYj0spzPvO+dXHrOWSDlSgct2e36xX4uv/lOfAj87UXv5Xd/8wxE4N7/+V8+fOPXyX3AGFAFA9x4yYW8dctp5e/746jC9T++l8/+679Rr1ZXuhOKiBBCpl5P73352qfKGIgaL0OkjQYEKLxnvN3iyredw8aT2mwca7Oh3Sr/jrX48Jtez6nrT+aUsTYf3f5GThlrsaHd4r3btnLmqzbTy3MiY0nygjM2beQDZ/3G4u/bLU4Za/OKk9rsPO/NtBp1XAgrlTlBVbFRVWqVl0O5QMjUkw7vQ19jMSKkRcFsJxk5SuED3bygVxR00nzZveeSBGNMOY8xHOqlI6wocbDTJS8cRoRVRAIEj8xMO4AIQPf9NOK3thsqFTQErDEkWcYlN32DD75hG0YEkZI8wHf/az9Th+YA5aJ/uJX3bNuKiPAfjz7OL56aoh5XcCFQr0Q8Nn2Qi264jfN+/fQyLvrj+BD42n0PUDhPLa6s0IUUjIWsh+5/OFpYjPZlV2/xlkcWeyEiQpLlZS4YhLU0qzGI0E0zmFcYY2jUqsu6ikC3l0EYzidSiWjE8cLCrBCKsYIvXpdcf+2DI+sLAFWlWY0xAwaVIyg+KKjSrleRvlMopeosHwfajfpIHw+qq5PREVgkMCLjBu1r7FEwIk2M6LPG2VwXl6MkUI3BnKhy5xhgLLgMmCfw0F6QxVUSymATBEVX66MvCpZZIAb6sVkS2LsbsUsJCKnrEUJAxFCN4j6ZE1eWiiwvnFUtME8grpafBFAl944tr3wdzWqTzOU88dwTBA0YMciL6GohBAY3uzRJCd6xtHgm6BICABowGLpZl8u3f4w/OffjoPBM5xn+4PZL6bkU9Uq3lyxTnePFYpUFzUYLa+2Ay5bPMFnSJUu6/Z2Yb0sJAEEDcVTl/K0XAJC7nCIUiAjOOcbXjXPeW7eja2L6chqqgT33P8Bcp4O1dsk9RRGiao28lwyxHsoDIkLmMoKGhUAWY0h6KV/6q09y4e+8i1wVK2vnSl6VWIQv33wLf/qJT7Fu3cn4QX0+gpCMJGCNJYRA0LKpKpE13L37Xn5t82Z6vR7GrN0zv2qgWq1x30/2EFWiVaneMgIihqxI6GRdxlsvp1ekpD4HhUazyc1f/wb/dMedGDGsTQQsTEzwnsI5Ws0mqrpcdaRfm5cHDsumHtgBxWD43A+u4+Dz/0ejUuO53iHSXpfC51SMJTiHX/MYABBiY8jT3vAthTwbcX2QgKoSRzGPTD3M1fffQVw/GXUpIe1C8ARjsWbtVt/7sCJBUFVCUcCIuBtwIYvLDnH6+dexefsVhABFZ5b/vuUC1CVkhSdZIqPHjtJFWq0xjDVHDNAlhpH3EtLO3EBCG9oBj6k0aL9mB0Xq8T7Dp4cBJctyTj31VLa/+U2EEaXxKmzHWEO3m/D9u35EmqYrEgRbiReNP5qMAgSXgggitjyXMYY0S5n85FW85x3nHbvxA9j5qT/n77/yVdavWzcsm4NYqYyCYGwFDQ4Nvt8CURRx+x13IihpliPm2BxJVRFjKPKce/fspV6rHdeODsloKDoUc9OsO20boYgpdD2I0Gw0+M73fsC3dn0PY+QF3fZoECkDOI4r1Gp1VANyFDcSEbyGF5ZRRRFT4fFdn2Bqeh+m2kbTQ2SdQ6jPiWyEWFl+KLla4ynnr0QG9Y60WwzaNNxflSJLV6BCqgRbQeae4twHr+ekKKYTAruKFNdPJMaY41JRHfhr+l/mic0fhi3LxqroEdxsGQEjQuIK/mbr2Xzk1WeA9zzrUnbv200SHEWW0+2lQ1K2llCUVqNJpRKVbirl80meJvTmDh9dRr0qVWM5s72OJO2Sa2C2yECVNE3ZtnUr57/j7Tjn1pyEoogYnHP88798i6npaSqVChoUFcHYaOUymgWPFSFCsCIYI2RZzlV/tpMLzj13TQ0fhWocM3nd5xhfv/4F5XUkgdhYnCpOFa9KCEocV/jiDTfy5NNTFEXRj6e12wWdP/b0gTu//R0ajcaK5HUoBnre8WhvjrNetrGsf2x5KldvNLjnvj3cdffuUkbXzPRFCBCCUq/XqNVqaNAy34jgg1+sUgdlVPsHLapK1Vo+vm8vdz15gLGowlxwHO4doiBgxdCII9a0lB5BIxQ5vaWyqYrLs+Vxp+X2RwBFFNJIxSFE6oAxobfdEjUjel2FuwDHQvbV43SdFywbYKTms6C0igmaLhDIarWpqJdNi5hNeZHrNVe+QS5+9xbS3DE9m7H7/rtJUk+Wp+R5jhzjA005u9BqtValYiJCkaYkh59HjEFDmKmJe7LTJ2D51a9S2bDx3xV+v1q14dWbxszTMwneBQ4+n6Gq9HoJO95yDu9797vI89UHsWqgGld5eP8v+cqttx1phUf/VsrnchHxAhbknpmZmTnALgaxlS8Q9IMo5IUnsuWrOGtLGc2Lgsv+8BJ++23HJ6Op9+z64Q956ukDxKs/mQYQRb4w/zkCPGA7Bw7sbm7YeBPGXNpsRHkIGntfnkKHoFSrVT77+b/mpz97COfcqlYQ+gIRxzzyy0c5MNVPUqszPhdj4hDC7cmzU9+nrELc/A4EwLz2tOiKnzyUnHb/vpkdZ52xwRfOk/nyVWytWpV9+/ez94EHjzkLq0IUWVrN5oq6L9gmgmqIg3f/WQ/+j5LS+KGacj5BN8Y3bfrijrM3XzLWjOkkOT/a8wzOB7XW9AP42KEoYWXH7f33XQY04LL8tk6RXsbs7OElto74X4kF5vbtRK0/pmLf0mhVNgrIS31Grcq0wD3O+xvy5w7uGrZxtIzIxATymWsIIvDGs7e0f/7E3Ct9Qf0lsboPE2kaN5tPzz722OH5Syy+wV8RbL+daBzVjv8HvXo66RzKheUAAAAASUVORK5CYIKJUE5HDQoaCgAAAA1JSERSAAAAQAAAAEAIBgAAAKppcd4AAA3ISURBVHic3Zt7jFz1dcc/53efM7O79tqNH2AMJiUPSCDYMTVpKBGlAZoUQagJSpQgETUKVE3rBGhUKoxFKC0tFIrIEyVFbarUbkrVBpoIyrMKrxKVFDC4xCTGeO31a3dnd+bOvff3O/3jzuzDO7P2etdru19ppJl7f/fc3zm/8zuv3xnhoFBh7SbD6a8IfX1y8PHHAJYuVV49Q9m01oHo4ZIR1m70ZnNeRwVrN3qgHReu/Y316w0bNjgArrmhu9JTXu2sniHKAlV7jGuBwRgGEP8VX9wLg/dsGABg7VqPTZvsgaMnM9Mc2Pv5P56XRNGNgnwGz5wknn/Epz6rsBZnbZ/AP5hG9c+Hv3XXHtZu9Nh05QQhTBRAk/nStV9ZLUH89xKE79K0ATZ3gJvL+c8CDJ5nJIzRLP2lyxpXJ1+//ckDNWFMAE21L//+n6zEDx9HpIcszVB8RI5xte8AVUXI8YMAJHFZelHytdueGi8E0xwpAAv+YH0PeP+ESA9pmoMExy3zQDF3CciyHDQWMRsrX/jyIk4/XdGC50IA62/x2LDB1W1+o5RKK0jTDJEJm94zBn+Kz3gpCUy+P4UcRWRK2p4xMxWET5bmplRarKZ0Mxs2OK7cZFpzFUC5bn1XCfuGeN4iXK4gE95aqyegnV1qXIrxRFAKzavXkwn3wygi8D30ABoiQm4tjaQxFQOUS/H0mJ4MRQR1WvVy753D39qwB1XxWbvRsOlKWzH5OeqFi8lSh0xk3jnHuovO5+zlJxZ0muutKCBs27ufux55mlqWIUDgedxw2UWctuhXUJTd1RHufvQ/2TVUJfD9USGICHlumV8u8aVLP8rS+d3F9Qn04Zmt27j/qecJ/BmFJYI6a6K4x0r9POBBbrnF8zn9FQFwqmca31fNUkdza/jGMDRc448uPp+7rvz4lNRPmN/D5x7YhCrc9smLWXfhhyfcP3PZUi65+/6muhUwAlmec9+nL+OKle/rSPsza1bSyHK++9RzdFXKWHeYDklRjFGcnAU8SF+fmLHwVhbQNjBSzj11OZl1JFlObt2ET2otmbV88JRlREHhMNasOInMWhpZPnr//ScuYX6lTObc6Etyp5TjiJXLTyCzltTaSfRb71xz6nLUaYfIbVoQEVkAwNKl6rN0qRZsykh7qSnVpEHgGUQKrRgPp4oRIckycutQVUbSjMDzsOIQEYwIuXM0shwzzhhKUwMauSXwvFFaE2dbGOChpMGY7swMKhS89vWJ4YeFBpjXfxZhbfHGccwFYch9j/+En+/eixHBabEzW8ZOFfYM17j94SdQLQR0x4+eYOdQFRFBmwK87aH/YCRp4DcNJYCHkOWWP3v4MQZq9Sa9MfpOFRFhc18/9z/9HGEU4aYwxAeFAM4hb7waAU0NWAW8CDqv1xyoXk6VMAx4aXsfa26/j2W985pmrynJ5vc9wzW27x+gEhV0H938Bqtu/RsW9XQBMFRvsHXPXipRiB3HgFWlFEf83bM/5an/fZPecmkSfRS27RtgqJ4Qh8HMBNCi3NM7qsZjvn7ZKeB5kOoELVBVymHIcJLy0va+tmQD49E1bnUqUciekRp9Q1WgUOGuDqunqnRFETsGhti2b6At/cj3Kc2YeQo3bgwsWT56aUwAeQ5+2PY5p4rnCRWv/f2Wuo4fH3geoee1vd+Ofuj7RB3nrTNnfjzsWMxxyCme6phfPrTx0xk9/fEzw5iGjwnAuSaXczeNOUeLPzfG45gA4hiiGKzrVCY5/qE0eRwrCfi8+GLxbeuW4mY6RUz+/wFhBI0mj088MSYA89TDiPGKrTAORsyYV1BwerzVRQ5IvoxB8+a1J58ctwXCuHAR4wQgYqilNawrVEYwlIIZZ2VziklpuDHgKwwWPycaQZFxRlBJsoT3LT2D3vICVB2pzdjc/1qhBceBnVBVXJ4xYbKqExa5rRs0YqinNf7wgnV84bxryZ0l8kN2DPbxqX+8mnqe4ImHacaORXo7lxLR0TDb6dQJUlqvk4xUOxZkJglAEBp5g5MWLOfqX7uaJEtIbYp1OfWsVjCriqBUq8PFMzOt2EwHzUA1zy1RFBGGwaQiy3gEcUzWqOPyfEKE28JkDZDC0FXCMg4FtRgxox8AYwz1JOETl/4OV11xOY00RUTmRAcU8DyPvfv28xd/fQ+7+vsJgqmFQDMBaze/jpFgoVoTKzMFLSHPc3rnz+erN9/E4ne8gyRJMHOoBXmeM69cZvfu3dx0620sXLAAayedeRwSpgyFdVz5a/SaKp7nUR0e5pHHHufS376EepJMyuOPLIShapWfPP88YRhOvfoHQUcBKOCLT2pTnDqss6PukKZ2fOXmDXzzO3/LXBtAYzwGhwZ5u28nlXIZd7glMtoIQFWJ/Iht+37Jy30vc/5pH2EkHSEOYmp5vTB4IgSBj1PHlje2ju6vucgiivcUWlgul1HnOpfcRbBZhsvzQ/cCxXNCmqdc/+D1fGrVJ5kf92BEGEyGSGrDZDbDNgl60qqszxUKcavNadSygwxV0qRe+P7pCEBVCf2IXXu3csfj9+KX5qPqEJQor4+u9rEeC7VKbFMdynTQAINtDHPC6s9x8oU3FydkXkhW3cHWH1yF5nUQgxGZkxjAOYe2ItXpQJVGvUZar01nCwjOpoTzTuSk37oViefhshriR+i4ipCIoZbUSZKkeY5yJCyAoOooxSXiOJq+tTeGqFwhTxuos7TT2baBkLoMv7wQ8QJcWlSQVS1oMykyhiRJWPWBs7j0kovJbd6W+GzAGMM//+u/8fLm1yhNtyrcOoEyBmdtWwXq4AalyWzziFAdrWNEEcitpbu7i/vuvINfXbGCpNE4IltBnaMcRfzGr5/Lx373qtmtCzYxRSA09WlummW89fYOVpx8MnmeHzEB5GHIW9vfJs/zg4e8h4HOAhAD4qE2R1BUHagrKssipGnGdeuu55xVK2d9UmNzKCz5sy+8eGTo004AqhgvpDH4FtlQH+Ulp2IbFhN4uLCCiOCAKIoYGBriXx7692kb5+miXC4TNEPe6fVrCM5Z1NppBkLGx9YHePPBz3PCR25EwnmI8chHdpNnDbAZFocIdFfKh8PTtOCcI7d22mZWUdJaDTdFtNghEHL4YYVd256j/4HLi9S4GU2J8Tj2Q6ACo7WLKexTh4qQUMtTLli8gvWnnkmX5+GLYWeWcM2b/0NDLQYBkTlNgztB1eFcOzukNGo18rQxnYoQ5M6xIIz59nvP4dRyD7U8o2Q8etIQ3/dJHfjGo5FlVKtVjq5GKKVSiSgMi6xw/PE7UOrqZmQg62io2wogVceJcZmFQcT+NMGJolapu3zUECWNBksWL+a8D63pIP25QJEVPvPcC2zbvp0omhgtKhTb1vNwWdZWC6asCDla2V6xLQxSeAGrRFHEN+6+kzWrV1GvJ3NbF2xCnSOKI15+9TWu+PRnqdXreN7kRqypcFj9r0VVyDCvpwehsAMyqbvgyEMpErKe7m78Nh1oh4L2XoBixYVCE1SY0BXiex4jwzWuXfdlLr7wN4t63NHop1TFDwIeffwJ9u7bTymOpx0uT64IAYEY+hsJdZuzOCoxnGcEYgiaTDp1xHHEq69v4cX/fumoNpOqKnEct2e+edBTVI3aP99WAKEx7GzU+NKWn/LV084iNIa6WgZsNlpkcOqIo5ByfPSPypxq+7qguiIQmm4kaFWpBAHf376V7//iTXrDsDhSF8iD46d/oLAJemDf5wT4tCoZYgZaFz0RkkbOu5b1cs3a99LdHWIE9g9l3PO9LWS5wwiFizlC6l+41pn3BKVJHXtAUVTQgdbXUQ1Qp6+LgICxTokjn7tv+jAfOnsp1ZGUUuSzvb/GNx7chmtYAmOwzlGfqsd3mmhVfEUMpVI81iZ22AQFLwgZGdzfKo4UR0SG15oj1Kf5Rwjj81/OadUz0p0mub572Xw57ZT5vN0/gqIkqWVwOC26NRXSLCMMAlZ94MxZK5K2vE+aZmzesqVwrzPRsGYeYDwPm2UqIp6qTXPh2eYI1xKAN7xz5+7KosWPIOZywAJ+bhXfE5yCZwTPjJ0TiQh33X4bl3z0Qur1+izlBIpzRWh737fv5/Y776arUpnRwcc4ODHGOOuebvTv/AVFP7SbYAQt3l8Z5ROt322FL4K1lu6uCmvO+SDlOB5th50NWGfpiWPOXb16tu1LU0nlL5u/Bca8gAW8pH/HM/6SJd9BzDXGkAHBga5VVfF9n4HBQW7405tZe/llZGk2K3FQMcOiEvzN7z5QaNVsVJuUTIwJnLM/qO3e9WPAo+B5ght0gKks0i8mb+nZ1Zo92zOSGSNBo5FP6KBz6ijFMT969DEe+vEjs7pSAljniMKQ+DAiuzbIEAJ1bktQin6PQvVHiY4XgAKy62e7Rpa/Z/nHtr01+MN7v/fyynWffb+NIx/fNyYIxoyAqtLd1TXTyXWEdgpuDvFxmsY9azQCm2WbA9GPD27btp/m3m8NbLd0BnALFy7s3ptw77vf2Xt1b0+Mc44sd/bn24d17ltipgURwUMMqg6b5Ru9xFxXre7YywHMQ2cuDOBEQM28C4m9L4pnLjDGVOLo2P83rTqXiPKkwr21Pbseal6exDxMvYzNWK8QxKKTTzk5zbLVaWbf44SFB3n2qMAo+1TM677mLwz2929tXWYsmT0seIz+v/C4gqGY+5SYzioajh9BHPJfff8PJxHD4y8b51QAAAAASUVORK5CYIKJUE5HDQoaCgAAAA1JSERSAAAAgAAAAIAIBgAAAMM+YcsAABg2SURBVHic7Z17mGVVdeB/a59z7qteXf3GhsZW5NEgDq0QWkFQZAioTIxpfIxKzBgdEFBDRHxAdQ2TEKNjIBAyQhJMvsmHXzeKgCMReaSlFaMBlLYdaCV0I9DVVf2ox32fc/aaP8693VVdr3tPVd+qS93f913ouvecffc9a+2191577bWhRYsWCxc5cuXqESp6AaKASPVfs8rsKEBPjwEMAL0bQ5CW9I8MwoZNhrXbBbD09tqZFzgTNmxyWLtdx1XkypuTXW35DENDMyq+RYWuLoZKiSJ/dXVhzPuqwiWbDZsvscS0DvEUoKfHjBZ68oovrnFwz1H0LaJ6CsJRqtox2na1iIsqCCKSA+1T5Bkc50dOqFuyt/Y+c/CyDRscNm8O6y29fuFs2OSw+ZIQIP3Jnosx+nFRfbskkmkAtRZsGNW7xewhAsYgxgFAy6UAYx4T1b/Plfo2cfvtfkUJ6rIGdSiACj0bhd5em/rkdeuNkb8Q13srgPplsDYEFBFBVZAjNsBcmCiKiKKVliXiipcAY7CB/5RY+8X8rTc8AIyz0FNRm5BGFZi54voejPSIcUT9UsXkiKm5rBazhQIWVfASjhhBw/Bv8rv5Ezb3lmtVgumFVinoqI/3ZIaSepckUxdrIW8rfZMzG7+kxQxRtYiopNKOlsuPE+Z+P3/bV/tqUQIzTckCsOJDV7cNJez/jYSf86P7WsKfN4gYwNFi3hfPW69O20OZj3/hKHp7bWWKPilTfSj0bHTo7bXDnalvSip9ruZzPog3u7VvMXuIp6WCbzzvZBLO/cde2pOqfjDZHZMrwIZNht7eIHP5F3pMpu1dms/5SEv48x/xtFjwJZV+Y3+bfxu9vZZNmyaV88SaUZlTZq667o2I8+8EQQA4k17fYv6hGkgq7dpi6fcKt91w7+jp+2gmFmil30gPBFtNIrk+Gu23+vymQtXieqJBsLMd59SBv9mYm2g9Ybxp2LTJobfXZvrthSaVWq/llvCbEhFD4IcmnV6TVfsRRJSennFyHK8A27crgKJXRM6H+r/biOAYU9fLSO2eo4nLl1lxOgvgmPrrL/PT4200CBSxl9HTY9i4MeQwq++Oubwyb+z++OdWlwxvV78sFSdPTQhgjJAv+4RBnW5pI2QSCYwIdhI3shFBgWypDPaw6a0ISc8l4bqEh39WI44R/NCSK5TrdmW7rks64cX+7iOCiMEvW/G8UzoGwtNHRP7t8DUD97BbDGBLrvd2SaYSWiwEiBx+zSTfJVhryeVLnLRqJa9e0l1XXYeLRZ7c9RJ53yeTTGDtWAEYEUp+gKKcseYYlrRlxnxeDgKefrGPgeER2jOpcfdPhzHCSKFEVybFma9ZTdqrdcKjWIXf9O/lub4B2tKp+RYJYcX1TBD4FwL/xtq1U1iACiqcVY9BE8Bai6py+6V/wIfPXEfKq0lvxvDErpf42D/ezdMvvkwmlTwoRCNC0fdZtaiTO//wEt524msnvP+3+wf53Lce4K6fPEl7WwZbY2t0jGEkX+C8ta/jtv/6exy/YlnddR8plrjlkR9x3XceJJ1IoPNFDaKWCSpvqbwz5qGMlbOqIKKpy7/0uJNInKl+OSSa/k1J9QHecekf8LGzz0Dh4JpFPRgRdg+NcMaf3UL/SA7PdUCVUJWEMWy99nJev2rllF0EwAU3/T0Pbd9BWzo1rUk2IhTLPievWsGPr/0kmYQ3afnTlQNw/X0PcsO9P6CjLTM/ugNVi+saDcNdBb//ddx+u08kd4Wxg0CpjBQTIqzSaEl3WkNgRMiXy6xdtZI/Ouv06EerYkTqfpXDkKO6OvjoWadTLhZxKoO9YrHIe954Cq9ftZJyGE56vx9GXdtnLzgnaoE1CNKIEPg+V513FpmEN2X5U71Ca7GqfPq8s1i+qJNyEMyPgaFQsQAsT3tHVUzboecySgGiNztfHG5HtaMahzBt+SKEQciapYujkXzlFQcjgqpy4oplY+NIrHLCimXYimJNhmMMChy7pJt0MklQgwJYVXAdXrd8CargxKx7dSazuC3Dqu4uymE4T7xmAqqIkHb8cicAPRsPVq3mEf50hFZnJQZEKq1pfPl2SuEfvB9q7vsPL382GqyqxupC5opxCpBMdpYRKdVb0GzNw1UVx4zXS8eYmh6sAmaC+6fDMWbWFLgWRZ0DfCdhxsl11JOKKj1wW2+eMBjGONQylFVVHNfh+b37sRoFrMQZAEJkjkWEZ/YMjO2/jfDsnoEpfQRQacXArn0HKJRKuDUIwohAEPLr/n2IQBiz7tUxwP5cnpcODJFwnHkyD1AwDhqGefviC8MA9G48WLWxTUWELuiSff1LiVrRtE/QqpJJJPjVS338w9afRa23Iqh6XwnHYffQCHdu/RmJVIpQldBaUqkU9zzxS7a91EfCcSa933OiCctXvr8lGr7UoABWFdfz+OuHt5Iv+1OWP9WrOga46eGt9A8Ok3Dd2A1hVlHACDIy2MXD31kavSnTjAH6fhtdU+MPsNaSTia48q57ueOxn1Lyg1gj6Sd2vcRFN/8DLw8OkfCcyJoArjHkfZ9333Injz7z3KT3/3b/IB+84y4e3PYMmRqmgBApQDqZ4BcvvMzFt36DHRVLU+9rpFjiz7/3CH/23UdoS6fnxxQQqFoA+nfDyAiHt2k57N/atXp1t7/6+OdY95ZuwkBrDeuuegILpfKMPIGlIJjWE7ju2KOPiCcwW/EEnrZ61SvHE6iqeAmRbT/DPPPLE0d273oWVUPFITRKuCog2nXZtd1lN/GcQDc2rFkBqoU1Yi0gd4TXAopl/5WxFhChgGAMjnFPHLn5+mdHxwpO7K+1FmKMpJVoOpj2PCSRqO/eyuBxqkFe9bP2ZGICX0N070wEEFrFEaEjnaTe2JeZfvcRx1rwy+Pert9hX8t3aW1euPlYflWJF8rm1llzBLVoTloKsMBpKcACp6UAC5yWAixwWgqwwGkpwAJnYj+AKqhtJXl4xTHeuTWxAiSS4DhRpo95EtfSYhaYYH1klAJUBL11K5KuhHWp0lKAVxC2Et/b23vwrfEW4IUXwIwsFE/owiIcP+SbvAtojQ9feQTj35p8EMg8XtlqEZNaLUA9RcrUGyNDW3fquhYNJLYCSJS8kFw5hw3Kh39YDc8lk2ifWQ1bTMlMN5/EUgBBsGoplAusO2Ydbzr2dFJe6mAQpKriGpf9hQPc9/++O6MKtpic0PcJ/fKMkrHGUgDFYtWy8Z29vP9NHyDhRNE/VW20Vkl7SZ7d+xu+v+vR2JVrMRWRmfVLJYrZ4dil1K0AjnEYLgzx2fM/xx+f9Qn6hnaTJTtmF5mqUnCTDBUG50do9CuS6Ll6qRSgFEaGY3UHdSmAiFAKSqzqPoYN6y5hb3ZgwkFgtLvHwTFTbyw2lVj6haQiQhTSFmf72kSotbjJFE4hjw2CuruD+hSASAFes+Q1dKY7KZQLmNoTiIzBGEOhWKRQKFS8jrGKaS4kahypZJJMpvb8BTUUi+N6hEFQt9823iBQZpYL2hhDNpfjpOOP54Lz3objOgti3an6xLb86Mf8+5M/p60tM+dd5BGJCp4KYwwj2SzvOPccvn7z1+jq7IwewkJYcqjI+jNXXM5nv3Q9/7zpbro6OwnDufOVNFwBrLVk0mmu+fRVZDJp+gcGcJyFk4XOWktbW4Y/veoKHnp0C8PZLK7jzJklaKgCiAh+ELB08WJWrlhOLpfH87z5kUmjQTiOQ6FQpLuri5UrVrBv/368zNx1BQ1VAFUl4Xn09fezbfuvuOj8d7BnYCDWfv5mxVrLksWL+cUvt/MfO3eSSqVmbTAYh4Z3ARBZgo03/iVLlyzh9WtPivICzEVFGky0U1v49XP/wRc23kA+X6CtjmxmR4LYs4C4WGtJp1I8v3MX7/3QpZz2hlPx5ste+gagKNt++SsODA7NufAhhgKICEW/hNX4FbfWkk6nCMOQx378+MLwAYwinU7NuvDjNqC6FEBVSbtpdux5hr6h3azoXEnRL07r8ZsIay3GGDra2w8lrVsg6Cx6AqvlhYEfqxutTwFQXMdlML+fW7fcwtfeexMApWBs7qFom7dFp7ESM8kn1AJAECOUcllsGB75tQCIAjzaU53c8/NvA/DJc67kqK6jMOJQbceRpUiR8tILaorXaKy1lLJ5/EI+9nOONQi0amlLtvOtp+7mkR2PcMKKk0h5qTHjAiOGgl8gPzwYq2ItpkHBhgHW2hk1stjTQKuWzkw3Rb/ET36zJdpDINXevBJSbhwyyc7YlWsxBRJlg48ODIvfjcYPCRMHvzCIOAmWHHUqxk2O3UkkBg1LlIZfjF25FlNjw7ByZGCDLYCIISgNsei4d7Dq3M+TXnYCYqpFCahFvDSlfTvYefclsSvXYmpsGFIu5PFLxcaNAcQ4BMVhuo+/kOM/eBeIgy0XOGj6F9SEbm5xXJd0RyciQrlYaMwsQG2Ak2jnmPP/ByCEhSHEqebUq1/4M8kuPvdESj9X09nqdyYybQTlUqw61KcAYgjLWTqPOZPUkuOw5fwo4ddHVejFUlTxZlYBI0IqlZozRTDG4HiJWF1B3SFhqMUkOxATPxmyiFD2fWxoOe41a3CbOCJIjFAsFnnu+Z0kE8nKb2n8j2moH4AZrAOICL7vs2RxN1/u7eHNv3NGZAGasBuoWi4FHvjBQ1z3P2+kXC7jzGGAR700fCFeRCiXy/Rcew3v+d0LZuzImEuMHMpI/tH3XcKnLvsEuVy+qeIbGlrTqulfuWIF6884nf4DB5pW+HBoyKuq7BsZ4W1vPYuOjvY5jfGrl4ZHBDmOQzaXY2homMXd3YxksxOeENJMhGFIZ2cH+/bvp1gqkUmn53ydv1YaHhHkOg5Dw8Pc8vU7uPWrX2bp4sWNrsKsokSHRWRzeW7533dgw+YQfJWGK0AYhnS0t/Ote+9jz8AA7/7dC3Cc6LyeZusNVKP0+MViic3fuZefP72N9va2pmn9MEcxgapKW1sbj/34cR7Z8sOmHgdA9HuSiSRtbc0lfKg7IMRinASlAzsJSyMV/3+8RFLWWtrb2ppe+FVmO8qnXmw4Qf6XGqjPAqhivDSFvTvYv/0eVq6/lPLQUMV2jz58xIIN0WmygzRba5mPiBiCconA9xu0FqAWk8jwwoNfwmtbxuKTLxrvxVMQD8L8oldMC5+fCKFfppiNtzUc4owBVBHjYoMSv970YbpPfCedrz4b46UPeb9UEdcjyO7BLxZjVazF1ChK6PsE5RIzGUHHdAUr4rgILkPb7+HAtrsnroAY8NpifUWL2pBR3sg4xJ4FGIUQJe+kwTOYCQaCAqSkOXziTYfOTuRFLAUwIhTDkIzj8OnVJ3B293LSo1YHLZASYVe5wLUv7piFaraYiDDw8YsF1NrGdQEClG3I8kSKzW84m/WLlmNtOEYbLeAZw45CFrd/Z6yKtZgeN5nES6YojAxF2UEaMQsQiQ5X/NoJ61jfvZz+YmHcIc0WyBhDNvCbZlm0KVHFOA6pto7Y4fd1KYBBKIQhJ7Yv4oIlr2KwXCQ5wUKOAE4Nx6g307r5kUZEYq0iqiqO5+F4HkG5fIQjggQCtRyTTJNxHLJBEHsAKiIMDQ0jRnCMWdChpNHevpCOjvhZVU2M/ZkQO1HkzBCJFlDe99738F8uuhAxC9tZJCI8vOWH/J9vbsKYqXMvzzYNXwxyHIfBoSEu+28f5csbr6dQqmwsXag6oJEFeNd5b2fVq45i459/mc7OTmyDgkrmJEfQksWL+eiHPsjQyAj5YrHpA0JmiqqSLxT4wHt/n3/652/y0u7dJBKJhoyPGm4B1FoSnkcymSQIQ5wGm7z5SHUA6HnewZxBIvLKUwBVJZFI0Nffz4MPP8rlf/gRBoaGKoknFy6htaxcvJh7/uX7/Pq550g1MKSs4YPAap7AG//XX4HA+W8791ChC00LNFrUMcbwg0f/ld4bvxKN5hs4NY6lAM4MTHY1MLRULnPNdRtZsWwZ7gJKEjUOAbXKnv4BEgmPRMJr6LOoM0dQtA6wzy/jz8BEVZWgq6OD4ZGRSqrYhdb8K1R+ent724y2lk2Xjmcy6lIAi5I2DttGDvCLkUF+p2sp+/wS3mGjeB31mgxVJVTFdeckLHHeMZM+34Zh4yKCjAhFG3L1jie5/7RzWZZIkQsDRovbAp7IuDWCiViwpn8WOLjBNpdFY+6wqlsBrCoZx+UnQwO844mH+Pyak1nXsYSEMSjRXjkL5I1h0C+3BHzEUEI/ShAR+PWvAVSJmSRK6XA9tmUHed+2raxMpisKcChFhAChKnk/XrRqiykQYFQU8qymiFG1NZUWqtLheZRKIX0HshN3+EbIpFt9/BGhkiSqHhQdJ9tx0hkefu1wJvXsECLdFfs9oUIIMDhSYs3RnZy29ljSSQdrOaidjiMM5wJ++ORAXZVsURuqig2DmgNBVMmpSQxW/6y+P1oBKsLeEoiu2IvIq5lkIC8CxWLIVR8+lf/+/lPo6khUtkpHn1tV0kmHZ3eO8NOrt8b6gS2mR1XxiwVK+dzUFwqAHsj3ewcO/+hwC+AAgSI7DPImncAl5TjC8HCZT3zgFDZeeQZ7DxQYGhl7cqhVpZh0GMq2BoFHmkQmirou5rKTWQIriKPo8/CbElFKgINzzgk7aBGeBD44wfuUfcuKZRn+eMNa9g8WCcPI3I+5TiNFcaZZ5xeRpkqmgCrhPNvNpNbipdL4peJkx8ZpJBH5eeXvKRVAAZxAHwu96J+jPxQRSiWf409ZxrLuFPligIkZzGGMwfd9cvl8rPsbjSq4jqG9vb3y9/yxbCKC47qTHRsngBjlhxPde7gCWIDh/Uc/lVn20vNiZE3Fx3iwmSrgujNbwjXGkMvnWbFsGRdfdGETJImKXNd7+gfYsvVHGMeZ04OeJmZCeSjgWLUjxpV/rbw3JtJkIgvgwhM+rPi2YK5WxioAzGyxqir8dW84la/f/DVWH300Vu2MziFsBJGTS7jvgX/hU9d8vllyG4UixlENv5/d3beXyKJPqQBQsQIScKd69k+IhD9ri7Wqius4XHfNn7L6mKMZGNjbNMfGqSob3v0uHv/pz7j9zn+ke9Gi+Z4PSBQV1Nwx2QWTKYCTO7Bne2bZivuNcS7WaJ/3jKVUDQlbtnQpa159LEODw011bFwYhuRLJU479fXNkBE3FBGjap/KD+x5mKghj9PWKd10Inqdqn1n5c8ZWwFVxXNdBvbu5fmdu3jzmWc0nQXIJJM89fS2ZgheUUDUyheJBD/hQ55MAULAyfX3P51ZvvJvjTFXqLXBFNfXjIgQhCE3/OVXm3IMsPn+7/LNu79NR/u8TgcXiBjXWnt/fm/fA0zQ91eZSqAWcNo0/HzO8p9F5PhKITMJCIqOTs1kePIXT/OuDR/g7Devb80CZhcLYqzafRq6l3FoDDchUymAAjowMJDNLH3V+zH2cUQ8ARsEamby46tKcGBwkLvu/lbschrJfPYDRGj1P1YEV0M+Utj/4ktM0fphepNuASe/9+Wn2pet/LDCpmTStTt2DtqBA0WzqCOBH9hYziBrLa7r0r1oUd33zhnz0BMI1a1lgQKhGOOqDT6T39v/PSL5TrkeX4sfNgTc7EDfZg3tlYmEY/YM5OXv7t4eLl6UwnGEMNSJX3bqVqKqhGHYPK95KHwxBr9YsGEQWOM4rlr7F7n+/puoQfhQ+6AuANzcwJ5b25Yv39fenvjG321+JpH03OAT7zvZnWw1sOtA/EiVFtOjqpTzuaCUz7liDGr1M7n+vpuoUfhQ/2TGBYKO5a9ab8X+Uy4fHrdmVbv9Tyct1XTSdeyoftFxhOFswGNPteIBjgSqasMgULWho0i/iv1YYc+e+6lD+BBvNusAYefRRy82YfiVfMn+UbkcVsNalahbicptRQTNNhodE4aIGFM5pu87oZFPFXfvfoE6hQ/x3RkHR5aLVq58qxrzJeD86vk5lQUkC0ho9bAski3qYHSEvSDiVLtUVf2JVbmx0P/yfZVrpxztT8ZMBCOMci+2L115jhouBS4UkZVIdHhkNDmZb1OmJqG6Z7L6LFUPCDxkLd/I7+37XuWq6jw/1kOejZbpELV2BejqWt3tp0pvFpW3qvJGgdeq0C3Qydig4RYTUxVkFnRQYSfIU0Z5zBJuzQ8M9I26NlarH81sCqLqax5boeOOS7Zns53im+Y+GKDBWCcYzCUSw7z4YuGwjyZ+zjE5Ei2x2jVU94jMv8lzc2E4ZOYPWtrZohGmWA77f4va0MP+36JFixazzP8H6/Id23En+i0AAAAASUVORK5CYIKJUE5HDQoaCgAAAA1JSERSAAABAAAAAQAIBgAAAFxyqGYAACwVSURBVHic7Z17nGRVde+/a59z6tXvGebBgLzlMSiKAiaikRAQn8TEDCq+Lg/xQTB6jTcYNMMoEg1RuRATJUY00STOXDTxk6tiFDWCL1CvIMNbBIZhZnpmevpRVV11zt7r/nGqm5YMTHVP16mqrv39fOrDdFOnzu5dZ/323muvvRZ4PB6Px+PxeDwej8fj8Xg8Ho/H4/F4PJ4lhrS7AfNEQNvdBo9nH0jXPKSdKgDC+vXC5uOFtXcKmzcra9cqGza4djfM49kn69cbNm8W1q5N7WvzZmXTJkcHjl6dIwCqwjmbDACbzrF7fc+7310cyq8sUJ1W6rXOabvHk8sr9ZoYq27suo+O7/U969YFDVFwnTKYtd+I1q0LYN1vGv3GjUH/LXcfbY0eK7E9AWefhQmGUXckIgOqHSekHk8DtYJsVmWaMPyJiD6gau6sjrg72bChMvu29esNm48XNp3T1plBuwRAWLfRzP3j+975vlVOiqfi4lcI8luoHi1RLsAYcKlYqk3AG7+nw5EgBBEwBlTRJAbnHsbIbQTmP0ysP5z6mw13z16wcWPAunUOyd53kL0ArFsXsGnT7GhfumT9i1XkdTj3UpPLrYKGoScJoBZFEST1/6kg4h2Bns5FAREHCpr+hGAIQkmFwaD16RpGbsTJDRXZ/SWuvbYGwLqNQdYzguwEYP36dH2/YYNj3bpcafXaNyByAcLzJYzQeg2staR/vEkNvQOWKB7PYqDqkBlJkEByeRBBk+RuVf2SqdT/rvwPf7kd+G+DZCvJxsBSZbMApT/+wEtVgr8yUfgMdQ7qNQUsQpDOmzyeJY+COhQIo0CiCJfE23Huw9Xatk9x3XVxVrOBVhucsG6dYdMmm7v40qNDU9hAYF6LOohjCwgipsVt8Hg6F1UHOIIglFwejes/lsRdVv7bD30bSGfOLdwxaJ0AzGl46ZL3X4iJ/lqCYEinq+kf4w3f43mcdGvLSS4XgKBJ/erKHcF7+d6GZO4MerFpjQA01jDLXn/JYHX58o+ZMLxQ63Vw1iIStOSeHs9SQJ0DQYolo3H9lqRmz6t/+or7WiUCiy8AjYbm3vrep4e50hclnz9Zq2WLzjr2PB7PvlBNJF8IXRLvljg5t/KpK2/kRetDvrchWczbLO40fP36kE3n2OLbLz05yPf9SILgZK1MJSCBN36PZx6IhFqbtgLLKBa/UXrHZW/hexsS1q8PF/U2i/ZJjZG/+PZLTyYq3CgwQhL7Kb/Hsz+oOoxRolxAbfqiyt9++O9Zvz5kw+LMBBZHABpr/t8wfhtb8Mbv8ew3qooxrhUisP9LgPXrDZs22aELLzucKN8Y+evOG7/Hs0iICM4Z4rqVQvG64lvf90dsWJzlwH7OAFRYd45ZlT+kMDlS+q4E0Ular/lpv8fTClQdQYAaM26S5EXlT15xx/7GCezfDGD95QGbNtmJwfy1ki+epPXpxBu/x9MiRAzWqhEz4oz54qo3vKePzZsFdMED+cIFYN26gA0bktLbLzvPFErnaaWcIGZRPZQej+cJiARan05MLv/MicH8tWzaZFm3acF2vLAL1683bNzoht6+/giNwms1rjdi+T0eT8sRE2q1kphi33nFt1+2jk3nWNZtXJD9LUwANm8WRLRmko+aMOrDWvxBHo8nUwxx4gjMR5Zdsn6QtXfqQpYC8xeAxpZf6e2XvdjkCn+ktWnv9PN4skbEaFJXUygeUU3iP0uP2c9/KTBfxRBU4fLLo+LO5HYT5o7WuK7+YI/H0xYUUIxJgtidMPmpK+6Z767A/Ax33UaDiJZG7bkmXzxG47rzxu/xtA1BVSXK5ZJA/xxIl+fzYH7Gu/ZOBXDoxTinftnv8bQZwWi9poh5dd8F71uVZhJq3hfQvACk237a97bLTjdR7jmaZvLxa3+Pp62I4JwzuXyf5oO3AbCxeV/AfAQAQF3AmyUMDdARec09np5HEKxFRd/Ai9aHnLNusX0AKpxzju274H2rwLw0nXL40d/j6QzEaFx3EuaO6FubnAaiab2NfdNc5N6LTgv4HokUg9+WXH4F09XMt/5EBCPSereDpq5V59yiZmM0jfY3te+i4FRxHVIDodv7vkdwEoah2vhs4FuzZcn2QXMCcPHFyve+h8WdnZY6yK6AwczDNx0nxPX6bJGQlhIEFPM5wiDA7uf9AmNwqkzVapDY5gqbiMFEIX35HNpGIej2vu8pBKNJjKqewbr1OTZsiNPR5qmfnSYEQOCccxzr1udwyalqrSBqssgobkRInKNcneZpK5Zz6lGH8YyDVjVatbj310ZHJdbxk18/wi33/5rxyQp9fcX0/y/ACANjmKxUCcOQ5x95GKcedShDxQKw9/bP3OORsXF+cP+vuWPLYwRhQCmXy9wYur3vexBDkqiY4MiBlRw2CffCXxh46piAJgQg/ZCB5fZwGwSHkcRp4Y4WExjDdL3OYKHAR/7wpbzulGexcqC/1bed5d7tO/nUd3/INd++hXwuQkTm9SAGxjBZrvCKE4/nspedzm8dcci87l+LE77+y3t4/7/fyJ1btjHYVySx2YhAt/d9byKg6iTK5ez09POBe1mPYcNTO+v3LQDrNgubQENzAmGUo15reaYfY4TpesxwqcjX/uQCTjr0IADszNqwlc+CpKPf0asO4OOveSXPPPhAzr9+I32FPCLNzeADY5icnOIdZ76QT577KiAdxezMxU/1GQ03QT4KedWJx/OiY47gFddczw/ue5CBvlLLZwLd3vc9TtpDhpOAzzVzwb4FoOFMcLi1Ygyq2tIAIBHBWsdAPsc33nUBzznkIGJrCY0hMNkFHTpVrHOcd+pJOOAtn9tEXyE/O119MmZG/rec/nw+ee6rZg02MIZwnv2WWMtIqcj/fef5nPHx6/jFw1spFHI41xpL6Pa+73nSqRKqurbxm32OFs18qw7AOX0WztHq7L6BCNXKNB/8g7NmH8AoyD6psBEhCgJi67jg1JM455RnMVWpEgRP3mVGhOl6nacfuJKrX3M2ThURWbDxhEFAYh3DpQKfefM6gsC0zPihu/veA6CiNgHkyOXnv3eADRv2eUKw6R4VZGS/27evezQ8zgevXM65Jz8bp0qY4cizN4yk0/c/Pu35hEHwlAZojBDX6pz/gpMp5SKc03Trbz8IA4N1jmc/7UDOOP5oqtO1lozG3d73HmDmsB4MJbYe0MSCbR/fsAobNujy8987gOgRqbosPP3QPhsjQr1e57Sjj2Ckr4hqyyccTbUJEU467GCOXL2CWj15UqNOrCNfLPCyZxzTaPvitEEb++Nnn7AWda4lK7Bu73sPNMKCVcKwVO8fPgqA9Zfv9wxAY0kilOGGurTsGxABnOP4NavSc44d4PWZ8UAXopCjViwnscleDUNESKzjgIE+Dl02gjQcWovThrTTjztwBbRoJOzmvvfMQVUxQWRcMgzs83RgU3M8yeUVkUUtSfRUWNWM6pbPj2YCclRb56qyGRhlN/e9p4EqmLApe53PIi+z56ITH8BOIIt+8X2/RHCuqa9yPgLgJdjj6RaMacpemxIALeQFiParQfPAK83eyaJffN8vAVKHTlP2um8BUJVBJqqoPooJyCIaIxDpyAexGaeeyGJHyj9OkIEDrJv73kO6F53E05IkjwKwdu1Tfp37EABRLr9ctnziE1XC8CExBqR1JwFVAWO4c+t2hPZvQ0Hq1JvZI79/dBdhEO7VQ66qhIFh52SZh3aPobp4jquZbcC7HhsFazFm8fulm/veM0PaYapa7jvquIcA2HD5/ggAcPnlCkQyum15GpDdnHNhIThVcrkc3733V4yVq4347/Z+4U4VVLnt11t4YNso+Vz4pIYdBoZadZqv/fKeRY1dn9kG/Ortm0nDsRfnc+fS7X3vaSBGpTyVr//dR5anv9i/OADBGB2EAbY+/PTGYZCWCcDMnu+WHbv451v/3+yR1HbiGgExf/PdH5DsY/R1TonyOT57861U6jHGyH4/sIl1BMbw/x55jG/deS/FQr4lB4K6ve89pLYpRmVirL/+0D1PT3+5YRHiAEDZs8thbcsLAFlViqUCf/GVG/nZw482YsJt5qORU23Ewhv+4Zbb2PiTX9BfKmKf4kiuU6WQy3HfYzt415e+imkEsizUYBNrCQPDnso0F35+E9a6lhpBN/e9B0DBGNizm2Y7qxknYPrf8d1Qn4YWlwFQVYLAMFmr85Kr/4HbHnp09kCKdY7EORLbwpdzONXZAynX33IbF16/kVI+15T/0zrHQF+Jv7/pB1z8z/9G0DhJp6pp2/fVfudmBSMMAsYqVV5+zWf56YOPUCzkWxoP3+197xFQB2OjEDR3Yn9fw4kAOnTIISNxtfYAZ716hGUrlSRueYK4uUkpPvDKM7ouKcVSSQjSjX3fuwg46+Qb/8ewe/vp5fHx75Cm7rdPccW+PrEhADt2PCDPO21En3OqUqtKq2cC8HhaqukuTUs1NyXYKYc/rStTgnVr3/ccqpDLwZZfO3PjDUZNdHp5fNf+CoAKiA69/NyReHLnAzK0bEQPO1pxtsn0tvtPtyemnEkKWvZJQZvDJwVdGKoQRrDtESc7HjOY8PTyTV/9zkwx3ye7rLmswIccghbWpjZfq5JlxPhMKq1cGFCISpmmpl6MB3DmM/rzeUxhfmnB220A3d73PYUI2BhWPQ09/FikUoebvrrPy5oTAIBaBUz7aoH8Rk69LqST8vzPl27v+95BIKlDlXS22QTNL+TFtHwL0OPx7Cci6VZgk/gkax5PD+MFwOPpYbwAeDw9jBcAj6eH8QLg8fQwXgA8nh7GC4DH08N4AfB4ehgvAB5PD+MFwOPpYbwAeDw9jBcAj6eH8QLg8fQwXgA8nh7GC4DH08N4AfB4ehgvAB5PD+MFwOPpYbwAeDw9TPNJQRuFGltSmdLj8SwO87TR5gUgyqXlhlxClmnBPR7PPFAgl4cms6o3JwAPPwylQppt1Odr93g6FwWiKK0R0AT7EIDGSH/zzUhQbvwkjbt4PJ7OY6b4TMNGN216ynfPYwkQ4af+Hk8XIALaXGGQ+TkBPR7PksJvA3o8PYwXAI+nh/EC4PH0MF4APJ4exguAx9PDeAHweHqY5rcBM0BknnEGCuqDkjyeBdN2ATBiEBGss8Q2RpoMNlIUQQhM0OIWejxLl7YJgIhgxDBVm8IldQr5fpaVlqGq+w44VEAE6xIqcdXHJ3q6CEHVpc85C5j1LjJtEQAjhsQlTNUnOenQkznj2DN5ztOey0HDB812zFPhcPRHffx4y21c+o3LKEYlnPpDSp7uQJ3DJTFJXCep1dJftkkIMhcAI4ZaUqMUFflfL/8zXnfSueTDAnVbI27yBJNTx0BugIHCAIggIk0vHTyediNhSBCGRIUiSb7OdHkSZ21bZgOZCoCIENuYwcIgf/faT/Fbh/82O8s7KdfLs76AZnx6Th2xjUlc0vpGezyLjT7uug5zOfrCESoT49gkzlwEst0GVIhtnY++6ipOOex5bJ/cjhFDaMJUAGiM5s2+/Kjv6XJUFYyhODiEMdnvymd2x0ACpmoTvP6UN3L6Mb/HzvJOoiDK6vYeT+eiijGGfF9/Uz6wxSQbARCwaukvDPLa576OWjKNER+D5PHMoKqEuQJBmK1bLhMrNBiqcZVjVx3LocsOZTr2AuDxPBERIYhyaIY7WpnIjYjgbMyzDn42pVyJscoYgXRGAI9A27ZgPNmR9dR6YShBmG3mrezmG6oM5AfaHvgwg4hgjCFJEmySICI+qHiJIaSGb4whiiJUFdfhSW2zto9MFxy2yTxlrSYwhjhJGNszzrKRYYaHhnDOdYw4eRYHRdO4k3qdHaOjFPJ5isUi1nbGc9gJZBsH0AHbdoExTJUrDA8NcvFFF/LyF5/JyMhwx48MnoUhItRqNW7+4Y+57vrPc/e99zE8NIj13zfQAYeBsmTG+J/1zOO59qqPctwxR1OuVLDWdoA0eVpBemxEePPrX8srX/YSPvChD7Pxy//G4OCgnwnQQwIgIsRJwvDQINdc9RGOPuoIto+OEoahN/4eYOeu3RQLBT525RX86sGH+Nntt9NXKvX8zK9n9uICY5iYnOSCN72Btcccw87dY+SiCDOfyEP/6tpXFIZUp6eJwoA/fefFPs19g54RgNhalo+M8NIXn0GlUiHMOODC036iMGRqqszJz30Oz1i7lkq12pbw206iJ/56ESGxloGBAZaNjJAkiZ/29yhOlWKxyMoVB2Bj/xz0hABAuidsrSVJ2nPs0tM5OOeI49gHgNEjAqCq5KKI7Tt2cOddd1EoFvw2UA+iquRyObZu28Zd99xLoVjA9bgvoCcEAB6vaXz9P30R5xQRet4D3FOoEicJgwMD/MvGG9i6bRv5RnRgL9MznjDrHIMDA9z0X9/n05+9nve8/W3snBinXm8uC5GnuwmCgDUrV3LT92/m05+9nsEBHwcAPSQAkPoABgcGuPKvP8GWR7dyydsuYs2Bq9Otop53By1NNM0dz8TkJJ/8zGe56pprcapEgfT89B96TABmyOfzXPe5f+Rr3/wWZ/zui1izenW6M+CdQksKBYwxlMtlvvP9m7lz89309ZWIwtAbf4OeEwAFUGVkeJjdY3v43Bf/xfsCljgiQiFfYHg4PfTljf9xMhWATkoCYq0likJG8sN+8r/EUUDV+TX/XshEANLEhwH3j96fZvLtEItTVf9QeDoIwVlL4whTJnfMZEhWlFyY5/atv2DX1C5CE/b89ovH899RbFInyxEyGwFQJR/meWT3w9z60K305/s7JjmIx9MpOGtJ4nqmzujMFuWKEpqI//3dT7Cnsod8kPflvDyeBiKGWqWMuqWYFpx0FlDMFblv2z18+BtXUMqViIKIxCW+xLenpxFjqE9XiGvTmW9FZ5sT0FkGS8Pc8LMvAXDZS97PSGmEcq3cdF1ASEuDJS7BOr+M8HQvM8Zer5SZLk+1JQ4l8ziAVARGuOFnG7n90dv5k999F6ccdjLL+pYTmuaaM1McdLA41CgOajplY8HjaQJNd6DimFq1TFLPdt0/l7YEAs3MBH6960H+eOM7OGTkEE446FkcteKopvwCipILcjwyvgU3XWM6dn5XwdM1qHPYJMYlyWzOwnbRtkhA6yyFqECBAtsmtvHw7odgXlN6BRPSl+tjmumWtdPjWXxktiBNu2eubQ0Fnhntc2GOQlSY17WCoCjWWX+Qx+NZIB1xFkBVsThkXpsS6ncPPJ79pM0CIIgJULVoUsPaevOXKogJkHB+MwePp910Uj3KtgmASGr4cWUnJixSWHYE+ZFDQR37DoVUkABb20Nt590gAfjZgKcrENRZnLWoOqTNB+TaIgBiQmxtEhMVWPOCdzNy7CsorVxLUBhu7gPUYXKGqYd+wq+/ci5B1N8QDo+n81FVnLXYuE69WsW59iWqzVwAxIQk1TH61pzI4a/8BAOHnIRLwCXT2PpUc+48dUA/Lq6ANk4b+m1ATxdhwpAgigjzBWrlSeJabekHAokE2NoE/U87mWNffwNh3zLiqXEwQaOCyzymQ2LSl8fTjWgaDCTGUBwYQmSS+nQ1cxHI0IIE5+oEhSGO/IPPEBZHSCrjSBA1DL8znCIeT6Y0hCDfP0AQZp+lODMBEGNwtSkOOu1SSquOJKlNIEGU1e09no5GgHxff+abA9kIgAjO1okGD2TZsb+PrdUR6YgQBI+nI1BVwijKfBaQiQAIBhdX6TvwRHJDa1Bb75h9UI+ncxCCKEeWW9rZDMMiqI3pW3MiJhRsrf37nzMYY2arBnlJag8zfe8a6+HeRQnCiCyfxGx3AZo87psFM2WhK9Vqo1qwN/92oij5XI5CoYBz/nRnVmRskZ3xpQZBwNRUGYBnrD2OFQcsx1pfNbhdqCpBEPDgQw9x3wO/olQsksvlfL2GDOicITkjAmOYmJjgpBOfzZ++8xJOfu6JFItFtKkQZE9LUEVE2DM+zo3fuomrrv0btm8fpa9U9FWcW0xPCUAQBOzZM85r/+gP+diVHyIMQ8rlMlNTU+1umoe0ZNsbXncOp73wBbz5be/gl3fdTX+p5EWghfSMABgRqtUqxx7zdD70/j/HWku5XCYMw1l/gKe9OOcY3bmLg9as5i8v/wvWvek8rHOzTlrP4tMzT74Yw/R0jYv+x5tZtmyY6vQ0Ydgz+tcViAi5KGLX2B5OOem5nP2ylzAxOUkQBO1u2pKlJwRARKjHMStXruDU33oelXKF0D9UHYsxBmsTzvzd0wiM8TsCLaQnBADS6WUhn6dYLHjvchfgnKO/v58gCPz0v4X0jAAYY5iu1ahWp/2avwswxjA1NZVuz7a7MUuYnrAEVSUXRezYMcotP/oxpb4Sia8K3LE45wiCkP/8zndTJ6CPz2gZPSEAkOZiLxTyXPe5z7N79x6KhQJJkrS7WZ45qCr1OGb5yDA/ue2nfPVr32BwYMCXcG8hPeMGd6oUi0Xuvuc+PnDFlXzsyg8xNDREuVz2PoEOIQgCVhywnC1btvK+yz/IdK3m4wBaTM8IAIC1luGhQTZ++Sv86sEHZyMB+/v7fSRgO5kTCfiFf/n6bCSgN/7W01MCAGCdY3BwkJ/94g7OveAifxagA9jbWYCSDwPOhIwFoDMMzFpLX18JgDvvvtufBuwAZk4DjgwP45zzy7KMyFQA1HWO023mASsViz4fQJuZmw/AO/yyJRsBUEWCiPLWn+MS7ZhkIIAfaTwdhGCTmCyHo0wsUXGYqEj5sZ9TH9+KBDmfx9/j+W8oNq6T5Vw0m6FYFRPkiCceY/fd/06Qz6HaOcsBj6fdiAhJHGOTOFNndGZzcXUOk+/n0e9+hMr2Bwjzg6iNs7q9x9PRKFArT2U+Mc5wMa4Yk8NOj/PAVy4kqY4RloZQGzf24P2SwNODiCAi1KYmMx/9IeNQYFVLkB9k6pFbuesff5+pLbcR9Q8R5PpBglQI5vvyeLoREcQY1Dmqk+NtKQsGbQgEUpcQFkeobLuDuz73ClaedP6CqwObqARCo+P8Jp6nO1BVXJL0ZnVgSEUgyPWjatl68yfY9qNPUVh2BPmRQxuj+r46Q0ECbG0PtfI0yMzWicfT6QjqLM5aVNP6GO2MQG1bKLCqBYSodACqltrYg1R33jOPDwAxARIWAL+j4OkeBBpr//bHw7T5LIDORgdKmCcMi/O92vsBPJ79oCMOAwmpN1KYnzEr4ANHPZ6F01YBMAgiULWW2Nn5RweK0BeE6UzA4+kaZHYZ0G7aJgCBCBVrSdRyeHGAkweXc1z/EE51ny5ABxTE8Kt6hY27t5EX40XA0zWoc9gkxiVJGvXfa07AQISJuM7a/mHWH3kCvzOykpW5AqbJjlBVJAj5wcRONlXHKZgI5wXA0zXo7FZgrVomqdd7ZxswNf4ab15zFB875rksj3JMJgl74nrTJmxVGQose+I6qKLq/AzA03UEUUQpGqZerTBdnlr6gUAzI/+b1hzF9c/4bco2YVdcJxAhmMcfL0A4z2s8nk5jpuBJrtQHIkxPTS7dUGDTcPat7R/m48c8l7JNqDtHKD4Xj6e3UefIFUpE+ULmVZAyEwBBiJ3l8iNPYFmUY9o5P4J7PA1UHflSH2KW4AxAgKqzHF4a4IUjK5lKYkJv/B7Pb2CCgDDKZToLyEQAjAixtZwyuJwVuQJxE1t9Hk/vIQRhjizPtWTiBBQAVY7rHyIQ6Rh/vYhgjPFi5AFSs1N1ONeuJ1QxQUCWJ1sz3QWwHZQHMAgCarU6lWrFJwb1AOmAUMgXKJWKOOd6oix5pgLQCSPtTAjm2J49HLxmDef84e+zZvXqtDaA90v0JEpajbhcLvOd79/MnZvvpq+vRBgEuCUuAh1xGChrarUaF/2PN3HJ2y5izYGrERFfGKTHSU+WwnsmJ/nX//NlrrrmWqana0RhuKRFoKcEIAgC9uzZw4bLLuU9b38bOyfG2T22p93N8nQQQRBw8YXnc9wxR/OGC9+KdYqRpZtupmcEIDCGiclJTv+dF/LW88/jsV07EYQwCNrdNE8nocrWHTs47YUv4K3nn8dV11zL8pERkiVasaj9KUkyYqbWynlvfD3GCKrpus/j+Q1EiMKQiclJXnfOq1mzejW1OPtsvVnRExYgItTjmFUrV3L8cccxXZ0m8MbveRJEhHq9zprVqznumKOZrk43fVK12+gZK1DS9V0YBj2xvePZf4wxRFG0pMvY9YQAqCphEDA5OcnusTHCMFyyTh3P4mBEqFar7BjdSRAt3eelJwQAIAoCdo2N8fVvfotSqUSS+EzCnr0TJwn9/X3c+tOf8cvNmykVi0s2WKxndgGscwwODPAP//gFzn75SznmqCPZuTudDSzN1Z1nISTWUiwUiBPLX1/zyY7I29dKekYAVJUoDNkzPsE733sp1171UY475mjKlQrWWi8CPc5Mbr7h4SF2j+3hAx/6MLf9/OcMDg5il+gWIPSQAEA6C+jvK/GLO+7k7Necy/lvegMvf/GZjIwML9kpnqc5RIRarcbNP/wx113/ee6+9z6Gh5a28UPGAtAJjpQZEahOT/OXH7uaT3/2cwz09+OcW7J7vZ6nRlGMGGr1OjtGRynk8wwPDy1544c25ATsBKxzBEHA8mUjJEnCrt27kQ46puzJFiFdIhpjGBkeRlV7wvghYwGYSOKOMbKZL9mIYKKo3c3xdAjtNvysY1QyEQAlLeT5o/GdVG1CJ0XfKyzpQA9PNyHYZKbSdTaz5UziAKwqxSDg9skx7q9MUgwC2pZ0xePpUFQVG9czrRqc2Z1CEaaSmL/fcj/5IMT6qr4ezywiQlKfxmYcoJaZAFhV+qMcf/fIPfzf0Uc5IF+g7rfePB4QwTlHrQ3VgTINBRZVIhNw/p0/4OaxHazIF3GqJKo4TYt7zffl8XQzIgLOUZ0Yb0ssSqYC4IDIGPbEMWf//Dtc89BdFIOQ5VGe/jAkEmn6FTZeHk/XIZKmoRMhqdcpj49hk/bkHMg8EtCpkg8Cpp3jT+6+lY3bH+LsFQfz/OEVHFroa2pUt6rEzjKRxI3ioOqLg3q6BrUWl8QkcZ2kVgPaVyK8LaHATpVAhMEoxw/2jHLL7u0UwojlUb5pQxaEWB3OWaZa3F6PZ/GQtJp1Y+u53dGnbTsLoDQcg2GEafx7Z1yb12cIdFShEY9n33SG4c8wHwFoiZ05VWZcH9ECOsUbv8ezV5oyjaYEQNUJ0JJ4WZE0+woy/4A8VXwUn8ezN1SbstdmBEAGB6nurrFFjByn6eJlUeYvgRHixDE1HYNTTNj8poQ2rs/njNcAT1fSgmWAAqLqqiYxW+b87knZlwAoYLZs2VItrVj9KHDcvj6wGQQQI0xM1Vk2XOC0Uw7it5+9iqMOHW6qMKOqUsyH3PHAHj7+hXsp5HyiT0+XIKDWkSR1bBzjrF1sIRBgOkf/o42f90sA5rIoFiaNqX65HPMHZx7Bn55/IoceNEBgBGubu4VzykBfRJDLIeGDRLmlXb7Js8SIIKKAqqNerVKvVljkA0CJtfWmztw1IwAGcCL8HJEzF2OordUsV7zreVzwR8dRqSZMTNVBm0+/5lwaOVipzokD8ALg6TqEfKmfMMpRmRxfDH+WE5FAlfvGxx/eQ8N2n+qCpmcAqnpXwz4XLFNBIEyM1/iLS07hra85nu27qhiTruXn/VlGMAu4zuPpJFQdQRRRHBiiOjHGfs4CFBHE6r00lu/7uqAZr5sCBLh7G6PsglpojDBVjvm9U5/GhevWMrq7mhpxh+yHejztQlUJoxy5Yt+izGRF9J6Zf+7rvc0IgAPQanivqtvZuGberVRNR+2LX//M1A/Aks+47PE0jaojVyxh9q9YrVFVp6K3znzsPi9opm2AmZp6bCfIPQ2P5byOLYkI1VrCUYcNs/bIESrVZEHTfo9nKSNiCKIIXViuDAWMqitLJfhF43f7/KBmN95Ta1X9UWPYntcMwBiwseW5a1cw2J9r2tvv8fQaYZhb6KVOREDll1NTj+2hyZn6vM4CiOE/VPU9zPMY8cyc4cCVpbQ093wubiGBMSDidxBahIi0Pclmd6GIMSzQzZY6AHHfBBJS297nDKBZAXAAuXrfz+tRZRuwmgVsXCZJZxjazFnsickpnLq0WKiqrw60WIjgrJ0tx2aM8YVXWo9RVWdSAYDFPAvQ+LBgbOxX430rV90oJniTOmfncT3QGU4/Y4R6PaFWr/G7v/MCXvWKl7F61SoSXx5s0TDGUK5U+M9vf4evfv0blCsV+kslrBeBVuFI1//3TI6uuA1GBWhq6jXv48BG2ORU30wXVhY2ItTrMYMDA2y47Ar+4JUvx5gAZ21DnTpjhtLdpKGeIsLZL3sJb3zda7j08g+y+a676fMi0CqcGGPU2v+AzXVSu24qu+h8BMABMhRFN+2uJw8YOEIbyrOABrcFJS38cPVHr+SVZ57B1tGdiOCrAi0yM5V2nConP+fZfP5Tf8srz3kto7t2k4si73NZfAJ1LhHD9Y2fm1bZ+Z4FCLds2VLtW7HqCwTBetJlQFcIQBgE7B7bwxtf9xrO+r3T2bJjBzlfEailBMDO3WM87eA1vOeSP+ZP/ux9FPJ57xhcXKyIGOf0+5Ud2++kifDfuczXeB1AgPsn51yN9DvuCjm3zlEqFXnNq19FrV4j2L+AC0+TRGHI5MQkZ51xOk8/8kiq09Mdkw1nySAiIu4zjZ/mZdMLEYBgYnT0AUG/JGKEeQYFtQMRIU4Sli9bxiEHH0y9VvchyBkhIiTWMjw0xOGHHkJcj33fLx4uHf3tPeVi8cukq695Ta8WMn1PS/3Z4EpVV2vctCtmAR7PEiP1tqr8JQ89NM0CwvQXIgAOMJO7tt4D+q9izLzWHO1AVYnCkF27d/Pwli3k8jmfPyAjVJUwCNgzPs6DDz1MlIt83y8Os6N/ZbT/X5nn2n+GhTrwZlIPXeqc200XzAICY6hUqnzphn8jn/OOqKyIk4SBwQFu/NZN3PfAAxQLBb8LsDiogqDmnXD/gmfiC00L7oCgMjq6rf+A1R8nNFeoczPhhx1JYi1DQ4Ns/PJXePlZZ/ptwBYydxvwgGUjPLJlKx+79m8oFos+InBxsCISOOf+szK6/ZukzvgFjWj7Y7AOCKZ2Lruqb+WuV4uYE1XVNhrTkQgQBAHv+rM/Z3xiwgcCtYTHA4GCMOAnt/2MSy//INu27/CBQIuDAjh0MsS9g/2cfe+PADTqc26uq1tzAYGbewa5I928TpVcLmJicop3/M/3sukr/+5DgVvAE0OBp2s1Hwq8eFgxJsTa902Mjt7Pfoz+sP9T9nQpsHPrz/tWrvxTMeEnnmop0AlLP+eUKArJ5SK+81838+3v/Zc/DLTYPOEwkB/5F41EjAmdszdURrd/knmE/D4Zi7Fmt0BY3rHj6tLKVS8wJnj1k4lAGHaGic0kER0c6PfHgVvIzHFgv+5fFJyIhOrcg1Et/xYWsOe/NxbLaWcBE9Xyb4kL9aONyDPn+gMUwMBjOyo41zkjrR+VPJ2FoM6xl1V0mpZPteIs54yPPzzGArf9nshixfErIOPjD4+Juter6hgiAY0GOgdBFPDTzaNMTNUJgk6RAI+ns0iS+hN/pYCKiFHlHdVd226jyWQfzbCYB3ksEJR37LjDWc5CdazxezdTyef+X+9h8wNjlIohtokKQB5PL6HqsHGMyKxZKpAgEji1F1VGt32eRVj3z2WxT/JZIKru2narKG8RI4bHC4tgnfLJL97RiGDoDKegx9MJiBjq1Uq6JZ2ipOf8I3Xu6sqOHX9PWqB30YwfWnOUNwbCqdFtN7jEXYDIJOlxRdvfF/HtWx7hM5s2s2JZEduo8OPx9DIiQhLXqVfLzMm67UQkcNZdXRnd/m4WeeSfoVVn+RMgqOzc/lmHO0NVx0QksFaT/oEcV37qp3z6S3eyfDhPPgqwTrFW05JfTb5s478eTzcjYrBxTHVynIbjzwJGRAKUiyqj295NaqcJLYhUa2Xobroc2L79J8Xlq8/SUL8oYp6OuiSfD8L3X/1jbvvljv0qDloqRtBI8Ondip5uQ9VRq0w1ioMCkIhIqDDprHtXZef2z5JO++NWtSELuwkAOzCwZrkrui+KMWfhnBMjTE7VzbLhAs87YZUvD+7pDfZeHlxpRPipuvsc+obq9u0/oUXT/ic0JxNmwxX7V65eryKXAwRCEicuqE4nglNM2PyKRElLjeVzxjsTPV1JY71vgUCMAec2StW8Y3Jy6y4yMH7INmZfGi9XWnHgWSJ6jRhzNOowIhYhmK8hq+JHfk+34gBEjFF144h+oLx9+7WN/7df8f3zoR1L5xBIRkZGhuIo/yGFt4pITtOCaEoHnyb0eBaB9DlPA+VA+bpx8q7JnVvv5fGMPpmNau3ync0qXN+qVc9A+SsR81KAOUKw4BpJHk+HocxE7okEIoJz7pc4/ldl57avN96T2ag/l3YamJAaeSoEKw88U1Xfa4yciczERJPMeZ8XA0+34RqvUNLMM6nhi36isn35FxpFPMyc92ZOJxjVb0x7+lYeeCbwRkR/X8QM0ji5hxcDT+czM9KnS1kRkXQwsyrchOMLldFl/9owfGjTqD+XTjKkmcNDClA86KCDpW5fK4Y/RDlFjAnmiIHyeMfJnBd01t/kWXrMXZ+7OT8b0gCe2SPmqm6zCF9D9Z/KO3bcPue64AnXto1ONJYZJ+CsMvavXr1WnZyJ6ukq+nxBDkjLKAOqaS8+vhvgz/h6WsnsXnW6jSeNLGiKqo4L3CnwTVHzn5OjW3/Mbw5UM0d42274M3SiAMwgPD5Fmu2wkZGRoWnJHWNC83wVPRTlBFF9uor0i2oeY0pta7FnSdNIdmoVnUCxImYz6AMKvxLcDyUIfjH12GM7n3DZzNHdjhyYOlkA5jJ7qpC9deSBB5aGoihfS5L+wNqjsm6cp1cIEbHbgmpumzGTbmxsbHwvb5oZuOb6AzqWbhGAuTzREdix6upZ8sw8hzOZeTve4J9INwrA3pjrAFwqf5OnM5kbqNNVxu7xeDwej8fj8Xg8Ho/H4/F4PB6Px+PxeHqC/w9eQ4CENLk0twAAAABJRU5ErkJggg=='
$IconFile = Join-Path $AppDir 'meet-board.ico'
try {
    $iconBytes = [Convert]::FromBase64String($IconBase64)
    if (-not (Test-Path $IconFile)) { [System.IO.File]::WriteAllBytes($IconFile, $iconBytes) }
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
    $script:InstalledVersion = Get-InstalledVersion
    Update-Status
    Log 'Checking GitHub for the latest release...'
    Check-Latest
    if ($script:Latest) { Log ('Latest release: ' + $script:Latest.tag_name) }
    $timer.Start()
})
$form.Add_FormClosing({ $timer.Stop() })   # closing the launcher leaves the server running

[void]$form.ShowDialog()
