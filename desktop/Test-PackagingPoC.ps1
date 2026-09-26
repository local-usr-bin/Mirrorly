# Checks evaluated deployment boundaries and actual publish resources. No GUI launch.
param(
    [Parameter(Mandatory)][string]$MSBuildPath,
    [Parameter(Mandatory)][string]$PublishDirectory
)
$ErrorActionPreference = 'Stop'
$project = Join-Path $PSScriptRoot 'Mirrorly.Desktop/Mirrorly.Desktop.csproj'
function Assert($condition, [string]$message) {
    if (-not $condition) { throw $message }
}
function Read-Configuration([string]$configuration) {
    $json = & $MSBuildPath $project "/p:Configuration=$configuration" /p:Platform=x64 `
        '-getProperty:WindowsPackageType,EnableMsixTooling,SelfContained,WindowsAppSDKSelfContained,WindowsAppSdkUndockedRegFreeWinRTInitialize,WindowsAppSdkBootstrapInitialize,WindowsAppSdkDeploymentManagerInitialize,DefineConstants,PublishTrimmed,PublishReadyToRun,PublishSingleFile,RuntimeIdentifier' `
        '-getItem:AssemblyMetadata'
    Assert ($LASTEXITCODE -eq 0) "MSBuild evaluation failed: $configuration"
    return ($json -join "`n" | ConvertFrom-Json)
}
$debug = Read-Configuration 'Debug'
Assert ($debug.Properties.EnableMsixTooling -eq 'true' -and $debug.Properties.WindowsPackageType -ne 'None') 'Packaged Debug was changed.'
Assert ($debug.Properties.DefineConstants.Split(';') -contains 'DEBUG') 'Debug diagnostics configuration missing.'
Assert ($debug.Items.AssemblyMetadata.Identity -contains 'CheckoutRoot') 'Debug development qualification missing.'
$release = Read-Configuration 'Release'
Assert ($release.Properties.DefineConstants.Split(';') -notcontains 'DEBUG') 'Release exposes DEBUG code.'
Assert ($release.Properties.DefineConstants.Split(';') -notcontains 'PACKAGING_POC') 'PoC leaked into Release.'
Assert ($release.Properties.DefineConstants.Split(';') -notcontains 'PACKAGING_WORKER_POC') 'P2 leaked into Release.'
$workerPoc = Read-Configuration 'PackagingWorkerPoC'
$w = $workerPoc.Properties
Assert ($w.WindowsPackageType -eq 'None' -and $w.EnableMsixTooling -eq 'false' -and $w.SelfContained -eq 'true' -and $w.WindowsAppSDKSelfContained -eq 'true') 'P2 must remain unpackaged and dual-self-contained.'
Assert ($w.DefineConstants.Split(';') -contains 'PACKAGING_WORKER_POC' -and $w.DefineConstants.Split(';') -notcontains 'PACKAGING_POC' -and $w.DefineConstants.Split(';') -notcontains 'DEBUG') 'P2 mode is not separate from P1/Debug.'
Assert ($workerPoc.Items.AssemblyMetadata.Identity -notcontains 'CheckoutRoot' -and $workerPoc.Items.AssemblyMetadata.Identity -notcontains 'Phase1AWorkerPath') 'P2 contains checkout metadata.'
$poc = Read-Configuration 'PackagingPoC'
$p = $poc.Properties
Assert ($p.WindowsPackageType -eq 'None' -and $p.EnableMsixTooling -eq 'false') 'PoC is still packaged.'
Assert ($p.SelfContained -eq 'true' -and $p.WindowsAppSDKSelfContained -eq 'true') 'Both runtimes must be self-contained.'
Assert ($p.WindowsAppSdkUndockedRegFreeWinRTInitialize -eq 'true' -and $p.WindowsAppSdkBootstrapInitialize -eq 'false' -and $p.WindowsAppSdkDeploymentManagerInitialize -eq 'false') 'Conflicting runtime initializers.'
Assert ($p.DefineConstants.Split(';') -contains 'PACKAGING_POC' -and $p.DefineConstants.Split(';') -notcontains 'DEBUG') 'PoC must be explicit and without DEBUG diagnostics.'
Assert ($p.PublishTrimmed -eq 'false' -and $p.PublishReadyToRun -eq 'false' -and $p.PublishSingleFile -eq 'false' -and $p.RuntimeIdentifier -eq 'win-x64') 'Unexpected optimization or architecture.'
Assert ($poc.Items.AssemblyMetadata.Identity -notcontains 'CheckoutRoot' -and $poc.Items.AssemblyMetadata.Identity -notcontains 'Phase1AWorkerPath') 'PoC carries development path metadata.'
foreach ($file in @('Mirrorly.Desktop.exe', 'coreclr.dll', 'hostfxr.dll', 'hostpolicy.dll',
    'Microsoft.UI.Xaml.dll', 'Microsoft.WindowsAppRuntime.dll', 'Microsoft.UI.pri',
    'Mirrorly.Desktop.pri', 'App.xbf', 'MainWindow.xbf', 'Themes/MirrorlyResources.xbf',
    'Components/SpringSprig.xbf')) {
    Assert (Test-Path -LiteralPath (Join-Path $PublishDirectory $file) -PathType Leaf) "Missing publish dependency: $file"
}
foreach ($svg in Get-ChildItem (Join-Path $PSScriptRoot 'Mirrorly.Desktop/Assets/Decorations') -Filter *.svg) {
    $copy = Join-Path $PublishDirectory "Assets/Decorations/$($svg.Name)"
    Assert ((Test-Path -LiteralPath $copy) -and (Get-FileHash -LiteralPath $copy).Hash -eq (Get-FileHash -LiteralPath $svg.FullName).Hash) "SVG missing/changed: $($svg.Name)"
}
$runtime = Get-Content -LiteralPath (Join-Path $PublishDirectory 'Mirrorly.Desktop.runtimeconfig.json') -Raw | ConvertFrom-Json
Assert ($null -eq $runtime.runtimeOptions.framework -and $null -eq $runtime.runtimeOptions.frameworks -and $runtime.runtimeOptions.includedFrameworks.name -contains 'Microsoft.NETCore.App') 'Runtime config requires shared .NET.'
# Read metadata without loading WinUI or starting an application/worker.
Add-Type -AssemblyName System.Reflection.Metadata
$assembly = Join-Path $PublishDirectory 'Mirrorly.Desktop.dll'
$stream = [IO.File]::OpenRead($assembly)
$pe = [Reflection.PortableExecutable.PEReader]::new($stream)
try {
    $metadata = [Reflection.Metadata.PEReaderExtensions]::GetMetadataReader($pe)
    $types = foreach ($handle in $metadata.TypeDefinitions) {
        $metadata.GetString($metadata.GetTypeDefinition($handle).Name)
    }
    Assert ($types -notcontains 'DesktopDevelopment' -and $types -notcontains 'PrototypeConfiguration') 'PoC includes development launch providers.'
} finally { $pe.Dispose(); $stream.Dispose() }
$strings = [Text.Encoding]::Unicode.GetString([IO.File]::ReadAllBytes($assembly))
Assert (-not $strings.Contains('Developer diagnostics')) 'PoC exposes the DEBUG navigation entry.'
Write-Output 'PASS: Debug / Release / PoC configuration boundaries and publish resources. Runtime smoke and clean-machine testing are still required.'
