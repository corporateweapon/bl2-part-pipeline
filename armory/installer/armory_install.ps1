# Armory installer for Borderlands 2 -- installs (or removes) the files of the package it sits in.
#
# Started by Install.bat / Uninstall.bat next to it (double-click). Works for the Armory itself
# and for every Armory pack: whatever is in this package's sdk_mods\ and WillowGame\ folders is
# copied into the Borderlands 2 game folder, exactly where a manual drag-and-drop would put it.
#
#   -Action install|uninstall   what to do (default install)
#   -Source <folder>            the extracted package (default: the folder above this script)
#   -GameDir <folder>           the Borderlands 2 folder (default: found through Steam, or asked)
#   -DetectOnly                 print the game folder it would use and stop
#   -NoPause                    don't wait for a key at the end (scripts, tests)
#   -Yes                        uninstall without asking
#
# Safety: it only ever writes into sdk_mods\ and into WillowGame\CookedPCConsole\ for files named
# Pipeline*.upk (the packs' own packages); it never replaces or deletes a base-game file. Windows
# PowerShell 5.1 compatible, ASCII only.

param(
    [ValidateSet("install", "uninstall")] [string]$Action = "install",
    [string]$Source = "",
    [string]$GameDir = "",
    [switch]$DetectOnly,
    [switch]$NoPause,
    [switch]$Yes
)

$ErrorActionPreference = "Stop"
$BL2_APPID = "49520"
$EXE_REL = "Binaries\Win32\Borderlands2.exe"

function Say([string]$text, [string]$color = "Gray") { Write-Host $text -ForegroundColor $color }
function Finish([int]$code) {
    if (-not $NoPause) { Say ""; Read-Host "Press Enter to close" | Out-Null }
    exit $code
}
function Fail([string]$text) { Say ""; Say "ERROR: $text" "Red"; Finish 1 }
function Get-Sha256([string]$path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($path)
    try { return [System.BitConverter]::ToString($sha.ComputeHash($stream)) }
    finally { $stream.Dispose(); $sha.Dispose() }
}

# ------------------------------------------------------------------ the package being installed
if (-not $Source) { $Source = Split-Path -Parent $PSScriptRoot }
$Source = (Resolve-Path -LiteralPath $Source).Path
$payload = @()
foreach ($top in @("sdk_mods", "WillowGame")) {
    $dir = Join-Path $Source $top
    if (Test-Path -LiteralPath $dir) {
        $payload += Get-ChildItem -LiteralPath $dir -Recurse -File | ForEach-Object {
            $_.FullName.Substring($Source.Length).TrimStart("\")
        }
    }
}
if (-not $DetectOnly -and $payload.Count -eq 0) {
    Fail "nothing to install next to this script: expected sdk_mods\ and WillowGame\ folders in $Source. Extract the whole zip first, then run Install.bat from the extracted folder."
}
$packs = @($payload | Where-Object { $_ -match '^sdk_mods\\ArmoryPacks\\([^\\]+)\\armory_pack\.json$' } |
           ForEach-Object { $Matches[1] })
$hasArmory = [bool]($payload | Where-Object { $_ -eq "sdk_mods\Armory\__init__.py" })
$what = @()
if ($hasArmory) { $what += "the Armory" }
if ($packs.Count) { $what += ("pack(s): " + ($packs -join ", ")) }
$label = if ($what.Count) { $what -join " + " } else { "files" }

# ------------------------------------------------------------------ find Borderlands 2
function Test-GameDir([string]$dir) {
    return ($dir -and (Test-Path -LiteralPath (Join-Path $dir $EXE_REL)))
}

function Find-GameDirs {
    $found = New-Object System.Collections.Generic.List[string]
    $steamRoots = @()
    foreach ($key in @("HKCU:\Software\Valve\Steam", "HKLM:\SOFTWARE\WOW6432Node\Valve\Steam",
                       "HKLM:\SOFTWARE\Valve\Steam")) {
        try {
            $props = Get-ItemProperty -Path $key -ErrorAction Stop
            foreach ($name in @("SteamPath", "InstallPath")) {
                if ($props.$name) { $steamRoots += ($props.$name -replace "/", "\") }
            }
        } catch { }
    }
    $steamRoots += "C:\Program Files (x86)\Steam"
    $libraries = @()
    foreach ($root in ($steamRoots | Select-Object -Unique)) {
        $libraries += $root
        $vdf = Join-Path $root "steamapps\libraryfolders.vdf"
        if (Test-Path -LiteralPath $vdf) {
            foreach ($m in [regex]::Matches((Get-Content -LiteralPath $vdf -Raw), '"path"\s+"([^"]+)"')) {
                $libraries += ($m.Groups[1].Value -replace "\\\\", "\")
            }
        }
    }
    foreach ($lib in ($libraries | Select-Object -Unique)) {
        $candidate = Join-Path $lib "steamapps\common\Borderlands 2"
        if ((Test-GameDir $candidate) -and -not $found.Contains($candidate)) { $found.Add($candidate) }
    }
    return ,$found
}

function Ask-GameDir {
    Say ""
    Say "Borderlands 2 was not found automatically." "Yellow"
    Say "In Steam: right-click Borderlands 2 > Manage > Browse local files, and copy that folder's path."
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = "Select the Borderlands 2 folder (the one containing Binaries and WillowGame)"
        if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dialog.SelectedPath }
    } catch { }
    return (Read-Host "Paste the Borderlands 2 folder path").Trim('"').Trim()
}

if ($GameDir) {
    if (-not (Test-GameDir $GameDir)) { Fail "$GameDir is not the Borderlands 2 folder (no $EXE_REL in it)." }
} else {
    $dirs = Find-GameDirs
    if ($dirs.Count -eq 1) {
        $GameDir = $dirs[0]
    } elseif ($dirs.Count -gt 1) {
        Say "Borderlands 2 was found in more than one Steam library:" "Yellow"
        for ($i = 0; $i -lt $dirs.Count; $i++) { Say ("  [{0}] {1}" -f ($i + 1), $dirs[$i]) }
        $pick = Read-Host "Type the number to use"
        $GameDir = $dirs[[int]$pick - 1]
    } else {
        $GameDir = Ask-GameDir
    }
    if (-not (Test-GameDir $GameDir)) { Fail "$GameDir is not the Borderlands 2 folder (no $EXE_REL in it)." }
}
$GameDir = (Resolve-Path -LiteralPath $GameDir).Path
if ($DetectOnly) { Say $GameDir; exit 0 }

Say "==============================================================" "Cyan"
Say " Armory $Action : $label" "Cyan"
Say "==============================================================" "Cyan"
Say "Game folder : $GameDir"
Say "Package     : $Source"

# ------------------------------------------------------------------ checks
$sdkMods = Join-Path $GameDir "sdk_mods"
if ($Action -eq "install") {
    if (-not (Test-Path -LiteralPath (Join-Path $sdkMods "mods_base.sdkmod")) -and
        -not (Test-Path -LiteralPath (Join-Path $sdkMods "mods_base"))) {
        Fail ("the willow2-mod-manager (the Python SDK) is not installed in this game folder " +
              "(no sdk_mods\mods_base). Install it first from https://bl-sdk.github.io/willow2-mod-db/, " +
              "start the game once, quit, then run this installer again.")
    }
}
while (Get-Process -Name "Borderlands2" -ErrorAction SilentlyContinue) {
    Say ""
    Say "Borderlands 2 is running. Close the game, then press Enter (or Ctrl+C to cancel)." "Yellow"
    Read-Host | Out-Null
}
foreach ($dev in @("PipelineArmory", "PipelineCharacters")) {
    if (($Action -eq "install") -and (Test-Path -LiteralPath (Join-Path $sdkMods "$dev\__init__.py"))) {
        Say ""
        Say ("WARNING: sdk_mods\$dev (a development build) is installed. The Armory refuses packs whose " +
             "weapons or skins it also provides. Move that folder into sdk_mods\_disabled to use the Armory.") "Yellow"
    }
}

function Test-Allowed([string]$rel) {
    # sdk_mods\* anywhere; in WillowGame only CookedPCConsole\Pipeline*.upk (never a base-game file)
    if ($rel -like "sdk_mods\*") { return $true }
    return ($rel -match '^WillowGame\\CookedPCConsole\\Pipeline[A-Za-z0-9_]*\.upk$')
}
foreach ($rel in $payload) {
    if (-not (Test-Allowed $rel)) { Fail "refusing to touch $rel : this package may only write sdk_mods\ and Pipeline*.upk files." }
}

# ------------------------------------------------------------------ install / uninstall
$done = 0
if ($Action -eq "install") {
    Say ""
    foreach ($rel in $payload) {
        $src = Join-Path $Source $rel
        $dst = Join-Path $GameDir $rel
        $parent = Split-Path -Parent $dst
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        Copy-Item -LiteralPath $src -Destination $dst -Force
        if ((Get-Sha256 $src) -ne (Get-Sha256 $dst)) {
            Fail "copy of $rel did not verify (is the game folder read-only or the disk full?)."
        }
        $done++
    }
    # a newly installed mod starts disabled in the Mods menu; enable the Armory once, never override a choice
    if ($hasArmory) {
        $settings = Join-Path $sdkMods "settings\Armory.json"
        if (-not (Test-Path -LiteralPath $settings)) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $settings) -Force | Out-Null
            [System.IO.File]::WriteAllText($settings, "{`n    `"enabled`": true`n}`n")
            Say "Enabled the Armory in the Mods menu."
        }
    }
    Say "Installed $done file(s), each verified." "Green"
    foreach ($rel in ($payload | Where-Object { $_ -like "WillowGame\*" })) { Say "  $rel" }
    foreach ($id in $packs) { Say "  sdk_mods\ArmoryPacks\$id\" }
    if ($hasArmory) { Say "  sdk_mods\Armory\" }
    Say ""
    Say "Next: start Borderlands 2 and wait for the main menu before loading a character." "Cyan"
    if ($hasArmory) { Say "Mods > Armory lists the weapons (F5 spawns the selected one) and character skins." }
    if ($packs.Count) { Say "Console: 'armory packs' lists what loaded; 'armory list' the weapon ids; 'characters list' the skins." }
} else {
    Say ""
    if (-not $Yes) {
        $answer = Read-Host "Remove $label from this game folder? Type Y and press Enter"
        if ($answer -notmatch '^[Yy]') { Say "Nothing removed."; Finish 0 }
    }
    foreach ($rel in $payload) {
        $dst = Join-Path $GameDir $rel
        if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force; $done++ }
    }
    # tidy the folders this package created, if they are now empty
    $folders = @($packs | ForEach-Object { Join-Path $sdkMods "ArmoryPacks\$_" })
    if ($hasArmory) { $folders += (Join-Path $sdkMods "Armory") }
    foreach ($folder in $folders) {
        if (Test-Path -LiteralPath $folder) {
            Get-ChildItem -LiteralPath $folder -Recurse -Directory | Sort-Object { $_.FullName.Length } -Descending |
                Where-Object { -not (Get-ChildItem -LiteralPath $_.FullName -Force) } |
                ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }
            if ($hasArmory -and $folder -eq (Join-Path $sdkMods "Armory")) {
                Remove-Item -LiteralPath $folder -Recurse -Force   # its logs\ and rendered code too
            } elseif (-not (Get-ChildItem -LiteralPath $folder -Force)) {
                Remove-Item -LiteralPath $folder -Force
            }
        }
    }
    Say "Removed $done file(s) of $label." "Green"
    Say "Weapons of removed packs in your saves lose their parts until the pack is back; their records stay in sdk_mods\_pipeline_saves\."
}
Finish 0
