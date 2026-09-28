# Offline checks for the Gate 6A/6A.1-approved portable redistribution bundle.
param([Parameter(Mandatory)][string]$PayloadDirectory)
$ErrorActionPreference = 'Stop'
if (-not [IO.Path]::IsPathFullyQualified($PayloadDirectory)) { throw 'Redistribution: payload path must be absolute.' }
$payload = [IO.Path]::GetFullPath($PayloadDirectory)
$bundle = Join-Path $PSScriptRoot 'redistribution'
$manifest = Get-Content -LiteralPath (Join-Path $bundle 'manifest.json') -Raw | ConvertFrom-Json
function Assert-Redistribution($condition, [string]$message) {
    if (-not $condition) { throw "Redistribution: $message" }
}
function Assert-PinnedFile([string]$base, $entry) {
    $path = Join-Path $base $entry.path
    Assert-Redistribution (Test-Path -LiteralPath $path -PathType Leaf) "missing $($entry.path)"
    Assert-Redistribution ((Get-Item -LiteralPath $path).Length -gt 0) "empty $($entry.path)"
    Assert-Redistribution ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -eq $entry.sha256) "hash mismatch: $($entry.path)"
}
$required = @(
    'THIRD-PARTY-NOTICES.txt', 'THIRD-PARTY-SOFTWARE-TERMS.txt',
    'licenses/dotnet/LICENSE.TXT', 'licenses/dotnet/THIRD-PARTY-NOTICES.TXT',
    'licenses/dotnet/NET-LIBRARY-LICENSE.html', 'licenses/tensors/THIRD-PARTY-NOTICES.TXT',
    'licenses/windowsappsdk/LICENSE.txt', 'licenses/windowsappsdk/NOTICE.txt',
    'licenses/windowsml/LICENSE.txt', 'licenses/windowsml/SOURCE-AVAILABILITY.txt',
    'licenses/webview2/LICENSE.txt', 'licenses/windows-sdk/LICENSE.rtf', 'licenses/microsoft/MIT.txt',
    'licenses/python/INCORPORATED-NOTICES.txt', 'licenses/python/EXPAT-COPYING.txt',
    'licenses/python/MPDECIMAL-COPYRIGHT.txt', 'licenses/python/HACL-NOTICES.txt',
    'licenses/blake3/RUNTIME-NOTICES.txt', 'licenses/rust/LICENSE-MIT', 'licenses/rust/COPYRIGHT-library.html'
)
Assert-Redistribution ($manifest.schema -eq 1) 'unknown manifest schema'
Assert-Redistribution ($manifest.files.Count -eq $required.Count) 'material count changed'
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $manifest.files) {
    Assert-Redistribution ($entry.path -cin $required -and $seen.Add($entry.path)) 'unexpected/duplicate material path'
    Assert-Redistribution ($entry.sha256 -cmatch '^[0-9a-f]{64}$') 'invalid material hash'
    Assert-Redistribution ($entry.component -and $entry.version -and $entry.sources.Count -gt 0) 'missing provenance'
    Assert-PinnedFile (Join-Path $bundle 'payload') $entry
    Assert-PinnedFile $payload $entry
}
$sourceFiles = @(Get-ChildItem -LiteralPath (Join-Path $bundle 'payload') -Recurse -File)
Assert-Redistribution ($sourceFiles.Count -eq $required.Count) 'unlisted tracked material'
$licenseFiles = @(Get-ChildItem -LiteralPath (Join-Path $payload 'licenses') -Recurse -File)
Assert-Redistribution ($licenseFiles.Count -eq ($required.Count - 2)) 'unexpected license file'
foreach ($entry in @($manifest.preserved_licenses) + @($manifest.payload_evidence)) {
    Assert-PinnedFile $payload $entry
}
$ownLicense = Join-Path (Split-Path $PSScriptRoot -Parent) 'LICENSE'
Assert-Redistribution ((Get-FileHash -LiteralPath (Join-Path $payload 'LICENSE.txt')).Hash -eq
    (Get-FileHash -LiteralPath $ownLicense).Hash) 'Mirrorly MIT license changed'

# Compare the actual published dependency set, not only project declarations.
$deps = Get-Content -LiteralPath (Join-Path $payload 'app/gui/Mirrorly.Desktop.deps.json') -Raw | ConvertFrom-Json
$actual = @($deps.libraries.PSObject.Properties.Name | Where-Object { $_ -notlike 'Mirrorly.Desktop/*' } | Sort-Object)
$expected = @($manifest.expected_deps_libraries | Sort-Object)
Assert-Redistribution (($actual -join '|') -ceq ($expected -join '|')) 'published dependency version/set changed'
$lock = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'Mirrorly.Desktop/packages.lock.json') -Raw | ConvertFrom-Json
foreach ($package in $manifest.package_versions.PSObject.Properties) {
    $resolved = $lock.dependencies.'net10.0-windows10.0.19041'.PSObject.Properties[$package.Name].Value.resolved
    Assert-Redistribution ($resolved -ceq $package.Value) "package version changed: $($package.Name)"
}
$config = Get-Content -LiteralPath (Join-Path $payload 'app/gui/Mirrorly.Desktop.runtimeconfig.json') -Raw | ConvertFrom-Json
Assert-Redistribution ($config.runtimeOptions.includedFrameworks.Count -eq 1 -and
    $config.runtimeOptions.includedFrameworks[0].name -ceq 'Microsoft.NETCore.App' -and
    $config.runtimeOptions.includedFrameworks[0].version -ceq '10.0.12') '.NET runtime version changed'
$inventory = Get-Content -LiteralPath (Join-Path $payload 'python-inventory.json') -Raw | ConvertFrom-Json
$pins = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'packaging/python-runtime.json') -Raw | ConvertFrom-Json
Assert-Redistribution (($inventory.pins | ConvertTo-Json -Depth 20 -Compress) -ceq
    ($pins | ConvertTo-Json -Depth 20 -Compress)) 'Python dependency pins changed'

$index = Get-Content -LiteralPath (Join-Path $payload 'THIRD-PARTY-NOTICES.txt') -Raw
$terms = Get-Content -LiteralPath (Join-Path $payload 'THIRD-PARTY-SOFTWARE-TERMS.txt') -Raw
foreach ($marker in @('LICENSE.txt covers Mirrorly', 'coreclr.dll', 'Microsoft.DiaSymReader.Native.amd64.dll',
    'licenses/dotnet/NET-LIBRARY-LICENSE.html', 'binary package license', '2.2.0.48161',
    'python313._pth', 'Rust standard library 1.96.0', 'ac68faa20c58cbccd01ee7208bf3b6e93a7d7f96')) {
    Assert-Redistribution ($index.Contains($marker)) "missing component scope: $marker"
}
foreach ($marker in @('you agree to the applicable terms', 'only to the identified Microsoft components',
    'LIBCMT / LIBVCRUNTIME / OLDNAMES', 'open-source licenses', 'licenses/windows-sdk/LICENSE.rtf')) {
    Assert-Redistribution ($terms.Contains($marker)) "missing downstream scope: $marker"
}
$eigen = Get-Content -LiteralPath (Join-Path $payload 'licenses/windowsml/SOURCE-AVAILABILITY.txt') -Raw
foreach ($marker in @('2.1.74', '1.24.6', '800ac32bc82d562c611d641b1112a8aa9f90c4f9',
    '1d8b82b0740839c0de7f1242a3585e3390ff5f33', 's390x-build.patch', 's390x-build-werror.patch')) {
    Assert-Redistribution ($eigen.Contains($marker)) "missing Eigen source mapping: $marker"
}
$rust = $manifest.files | Where-Object path -CEQ 'licenses/rust/COPYRIGHT-library.html'
Assert-Redistribution ($rust.version -ceq '1.96.0' -and
    $rust.sha256 -ceq '78c163fcec50e64bfd85fedb850c273595602fafa2b41f30f75d4e410b80ee83') 'Rust 1.96.0 evidence changed'
$crateNotices = Get-Content -LiteralPath (Join-Path $payload 'licenses/blake3/RUNTIME-NOTICES.txt') -Raw
$sbom = Get-Content -LiteralPath (Join-Path $payload 'app/python/packages/blake3-1.0.9.dist-info/sboms/blake3.cyclonedx.json') -Raw | ConvertFrom-Json
foreach ($crate in $manifest.blake3_runtime_crates.PSObject.Properties) {
    Assert-Redistribution ($crateNotices.Contains("$($crate.Name) $($crate.Value)")) "missing runtime crate notice: $($crate.Name)"
    Assert-Redistribution (@($sbom.components | Where-Object { $_.name -ceq $crate.Name -and $_.version -ceq $crate.Value }).Count -eq 1) "wheel crate version changed: $($crate.Name)"
}
$readme = Get-Content -LiteralPath (Join-Path $payload 'README.txt') -Raw
foreach ($name in @('LICENSE.txt', 'THIRD-PARTY-NOTICES.txt', 'THIRD-PARTY-SOFTWARE-TERMS.txt')) {
    Assert-Redistribution ($readme.Contains($name)) "README lacks $name"
}
# The complete checksum inventory must cover legal files, not only the binaries.
$checksums = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $payload 'SHA256SUMS.txt')) {
    Assert-Redistribution ($line -cmatch '^([0-9a-f]{64}) \*(.+)$') 'malformed checksum entry'
    $name = $Matches[2]; $hash = $Matches[1]
    Assert-Redistribution (-not $checksums.ContainsKey($name)) 'duplicate checksum entry'
    $checksums.Add($name, $hash)
}
$files = @(Get-ChildItem -LiteralPath $payload -Recurse -File | Where-Object { $_.FullName -ne (Join-Path $payload 'SHA256SUMS.txt') })
Assert-Redistribution ($checksums.Count -eq $files.Count) 'checksum coverage differs from payload'
foreach ($file in $files) {
    $relative = [IO.Path]::GetRelativePath($payload, $file.FullName).Replace('\', '/')
    Assert-Redistribution ($checksums.ContainsKey($relative)) "checksum missing: $relative"
    Assert-Redistribution ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash -eq $checksums[$relative]) "checksum mismatch: $relative"
}
Write-Output "PASS: $($required.Count) redistribution materials, $($manifest.payload_evidence.Count) component fingerprints, $($manifest.blake3_runtime_crates.PSObject.Properties.Name.Count) runtime crates; $($checksums.Count) checksums."
