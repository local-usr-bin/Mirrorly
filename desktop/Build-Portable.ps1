# Requires PowerShell 7, an explicit build Python and Visual Studio MSBuild.
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$MSBuildPath,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$CacheDirectory = (Join-Path $PSScriptRoot 'artifacts/p2-cache'),
    [switch]$Zip
)
$ErrorActionPreference = 'Stop'
foreach ($path in @($Python, $MSBuildPath, $OutputDirectory, $CacheDirectory)) {
    if (-not [IO.Path]::IsPathFullyQualified($path)) { throw 'Build paths must be absolute.' }
}
$output = [IO.Path]::TrimEndingDirectorySeparator([IO.Path]::GetFullPath($OutputDirectory))
$archive = "$output.zip"
if (Test-Path -LiteralPath $output) { throw 'Use a new staging directory; existing output is never overwritten.' }
if ($Zip -and (Test-Path -LiteralPath $archive)) { throw 'ZIP already exists.' }
$root = Split-Path $PSScriptRoot -Parent
# Same pinned wheel/runtime assembly and fail-closed qualification as P2.
& $Python (Join-Path $root 'scripts/build_worker_payload.py') --output $output --cache $CacheDirectory
if ($LASTEXITCODE -ne 0) { throw 'Python payload assembly failed.' }
$gui = Join-Path $output 'app/gui'
& $MSBuildPath (Join-Path $PSScriptRoot 'Mirrorly.Desktop/Mirrorly.Desktop.csproj') /restore /t:Publish `
    /p:Configuration=Release /p:Platform=x64 /p:RestoreLockedMode=true "/p:PublishDir=$gui/" /verbosity:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw 'Portable Release publish failed.' }
& $MSBuildPath (Join-Path $PSScriptRoot 'Mirrorly.Launcher/Mirrorly.Launcher.vcxproj') /t:Build `
    /p:Configuration=Release /p:Platform=x64 /verbosity:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw 'Portable launcher build failed.' }
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'Mirrorly.Launcher/bin/Release/Mirrorly.exe') -Destination $output
Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $output 'LICENSE.txt')
[IO.File]::WriteAllText((Join-Path $output 'README.txt'), @'
Mirrorly for Windows x64

Extract the entire folder, then open Mirrorly.exe.
The app folder is internal payload; keep it together. Do not launch its files directly.
No Python, .NET or Windows App Runtime installation is required.
Exit Mirrorly through its tray menu before moving or replacing this folder.
Local task configuration stays in %LOCALAPPDATA%\Mirrorly\Gui\Tasks on this computer.
Moving this program folder does not move your source folders or backup repositories.
Python and dependency licenses are retained inside app\python and app\worker.
'@, [Text.UTF8Encoding]::new($false))
& (Join-Path $PSScriptRoot 'Test-Portable.ps1') -MSBuildPath $MSBuildPath -PayloadDirectory $output
$lines = foreach ($file in Get-ChildItem -LiteralPath $output -Recurse -File | Sort-Object FullName) {
    $relative = [IO.Path]::GetRelativePath($output, $file.FullName).Replace('\', '/')
    '{0} *{1}' -f (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant(), $relative
}
[IO.File]::WriteAllLines((Join-Path $output 'SHA256SUMS.txt'), [string[]]$lines, [Text.UTF8Encoding]::new($false))
if ($Zip) {
    [IO.Compression.ZipFile]::CreateFromDirectory($output, $archive, [IO.Compression.CompressionLevel]::Optimal, $false)
    Get-FileHash -LiteralPath $archive -Algorithm SHA256
}
Write-Output "Portable Release staged: $output"
