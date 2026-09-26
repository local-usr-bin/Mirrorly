# P2 only. Build-time Python must have pip/setuptools; no runtime install at app startup.
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$MSBuildPath,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$CacheDirectory = (Join-Path $PSScriptRoot 'artifacts/p2-cache')
)
$ErrorActionPreference = 'Stop'
foreach ($path in @($Python, $MSBuildPath, $OutputDirectory, $CacheDirectory)) {
    if (-not [IO.Path]::IsPathFullyQualified($path)) { throw 'Build paths must be absolute.' }
}
if (Test-Path -LiteralPath $OutputDirectory) { throw 'Use a new disposable output directory.' }
$root = Split-Path $PSScriptRoot -Parent
& $Python (Join-Path $root 'scripts/build_worker_payload.py') --output $OutputDirectory --cache $CacheDirectory
if ($LASTEXITCODE -ne 0) { throw 'Python payload assembly failed.' }
$gui = Join-Path $OutputDirectory 'app/gui'
& $MSBuildPath (Join-Path $PSScriptRoot 'Mirrorly.Desktop/Mirrorly.Desktop.csproj') /restore /t:Publish `
    /p:Configuration=PackagingWorkerPoC /p:Platform=x64 "/p:PublishDir=$gui/" /verbosity:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw 'P2 GUI publish failed.' }
$base = [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath($OutputDirectory))
$lines = foreach ($file in Get-ChildItem -LiteralPath $base -Recurse -File | Sort-Object FullName) {
    $relative = $file.FullName.Substring($base.Length + 1).Replace('\', '/')
    '{0} *{1}' -f (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
}
[IO.File]::WriteAllLines((Join-Path $base 'SHA256SUMS.txt'), [string[]]$lines, [Text.UTF8Encoding]::new($false))
Write-Output "P2 candidate assembled: $OutputDirectory (not a final release ZIP)"
