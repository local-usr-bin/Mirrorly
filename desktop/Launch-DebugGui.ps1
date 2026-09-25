param(
    [Parameter(Mandatory = $true)]
    [string] $TestDataRoot,
    [switch] $ValidateOnly
)

$ErrorActionPreference = 'Stop'

# This Debug build is registered as a packaged app. A bare EXE launch fails in
# Windows App SDK auto-initialization before Mirrorly's managed startup runs.
$packageFamily = 'Mirrorly.TechnicalPrototype_fhsq4wxgq3ejr'
$appId = 'App'
$exe = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'Mirrorly.Desktop/bin/x64/Debug/net10.0-windows10.0.19041.0/win-x64/Mirrorly.Desktop.exe'))

# Windows PowerShell 5.1 lacks IsPathFullyQualified; IsPathRooted accepts C:foo and \foo.
function Test-FullyQualifiedWindowsPath([string] $CandidatePath) {
    if ([string]::IsNullOrWhiteSpace($CandidatePath)) { return $false }
    try {
        $root = [System.IO.Path]::GetPathRoot($CandidatePath)
        [void] [System.IO.Path]::GetFullPath($CandidatePath)
    } catch {
        return $false
    }
    return (($root -match '^[A-Za-z]:\\$') -or
        ($root -match '^\\\\(?![?.]\\)[^\\]+\\[^\\]+\\?$') -or
        ($root -match '^\\\\\?\\[A-Za-z]:\\$') -or
        ($root -match '^\\\\\?\\UNC\\[^\\]+\\[^\\]+\\?$'))
}

if (-not (Test-FullyQualifiedWindowsPath $TestDataRoot)) {
    throw 'TestDataRoot must be an absolute path.'
}
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    throw "Debug GUI executable was not found: $exe"
}

$package = Get-AppxPackage -Name 'Mirrorly.TechnicalPrototype' |
    Where-Object PackageFamilyName -EQ $packageFamily |
    Select-Object -First 1
if ($null -eq $package) {
    throw "The Debug package is not registered for the current Windows user: $packageFamily"
}
if ($package.Status -ne 'Ok') {
    throw "The registered Debug package is not healthy: $($package.Status)"
}
if ($null -eq (Get-Command Invoke-CommandInDesktopPackage -ErrorAction SilentlyContinue)) {
    throw 'Invoke-CommandInDesktopPackage is unavailable on this machine.'
}

$actualInstall = [System.IO.Path]::GetFullPath($package.InstallLocation).TrimEnd('\')
$expectedInstall = [System.IO.Path]::GetDirectoryName($exe).TrimEnd('\')
if (-not [string]::Equals($actualInstall, $expectedInstall, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "The registered Debug package points to another build: $actualInstall"
}

[pscustomobject]@{
    PackageFamilyName = $packageFamily
    PackageStatus = $package.Status
    Executable = $exe
    TestDataRoot = [System.IO.Path]::GetFullPath($TestDataRoot)
    LaunchMode = 'registered package context'
}

if ($ValidateOnly) {
    return
}

$alreadyRunning = @(Get-Process Mirrorly.Desktop -ErrorAction SilentlyContinue |
    Where-Object { [string]::Equals($_.Path, $exe, [System.StringComparison]::OrdinalIgnoreCase) })
if ($alreadyRunning.Count -ne 0) {
    throw 'Mirrorly Debug GUI is already running; exit it normally before a fresh-session smoke.'
}

$args = '--test-data-root "' + [System.IO.Path]::GetFullPath($TestDataRoot) + '"'
Invoke-CommandInDesktopPackage -PackageFamilyName $packageFamily -AppId $appId -Command $exe -Args $args

$deadline = [DateTime]::UtcNow.AddSeconds(10)
do {
    $started = @(Get-Process Mirrorly.Desktop -ErrorAction SilentlyContinue |
        Where-Object { [string]::Equals($_.Path, $exe, [System.StringComparison]::OrdinalIgnoreCase) })
    if ($started.Count -ne 0) {
        "Started packaged Debug GUI PID: $($started[0].Id)"
        return
    }
    Start-Sleep -Milliseconds 250
} while ([DateTime]::UtcNow -lt $deadline)

throw 'The packaged Debug GUI did not remain running after launch; inspect the Application event log.'
