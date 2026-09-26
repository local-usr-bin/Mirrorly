# Artifact regression tests; mutate only a disposable copy, never the supplied payload.
param(
    [Parameter(Mandatory)][string]$MSBuildPath,
    [Parameter(Mandatory)][string]$PayloadDirectory
)
$ErrorActionPreference = 'Stop'
$tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$testRoot = Join-Path $tempBase ('Mirrorly 绿色 搬迁 ' + [Guid]::NewGuid().ToString('N'))
$testRoot = [IO.Path]::GetFullPath($testRoot)
if (-not $testRoot.StartsWith([IO.Path]::TrimEndingDirectorySeparator($tempBase) + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe test directory.' }
$validator = Join-Path $PSScriptRoot 'Test-Portable.ps1'
function Validate { & $validator -MSBuildPath $MSBuildPath -PayloadDirectory $testRoot }
function Expect-Rejected([string]$message) {
    $rejected = $false
    try { Validate } catch { if ($_.Exception.Message -notlike $message) { throw }; $rejected = $true }
    if (-not $rejected) { throw "Payload unexpectedly accepted: $message" }
}
New-Item -ItemType Directory -Path $testRoot | Out-Null
try {
    Get-ChildItem -LiteralPath $PayloadDirectory -Force | Copy-Item -Destination $testRoot -Recurse
    # Positive case also proves complete folder relocation and unrelated-cwd qualification.
    Validate
    $pth = Join-Path $testRoot 'app/python/python313._pth'
    $original = [IO.File]::ReadAllBytes($pth)
    try { [IO.File]::AppendAllText($pth, "import site`n"); Expect-Rejected 'Python import isolation changed.' }
    finally { [IO.File]::WriteAllBytes($pth, $original) }
    $stray = Join-Path $testRoot 'development.cs'
    try { [IO.File]::WriteAllText($stray, '// not a runtime file'); Expect-Rejected 'Non-runtime file in payload:*' }
    finally { Remove-Item -LiteralPath $stray }
    $readme = Join-Path $testRoot 'README.txt'
    $original = [IO.File]::ReadAllBytes($readme)
    try { [IO.File]::AppendAllText($readme, 'C:\Users\sakur\developer-python'); Expect-Rejected 'Build-machine path found in payload:*' }
    finally { [IO.File]::WriteAllBytes($readme, $original) }
    Write-Output "PASS: 4 portable artifact cases; relocated copy: $testRoot"
} finally {
    # Checked absolute testRoot stays under the normal temp directory; preserve input.
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
