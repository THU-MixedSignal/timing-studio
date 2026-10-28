[CmdletBinding()]
param([string]$Destination)
$ErrorActionPreference = 'Stop'
try {
    if (-not $Destination) {
        $Destination = Join-Path $env:USERPROFILE '.agents\skills\timing-studio'
    }
    $target = [IO.Path]::GetFullPath($Destination)
    if (Test-Path -LiteralPath $target) { throw "Destination already exists; nothing overwritten: $target" }
    $source = Join-Path $PSScriptRoot 'skills\timing-studio'
    $manifestPath = Join-Path $source 'install-manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $manifest.files -or $manifest.files.Count -lt 10) { throw 'Incomplete install manifest.' }
    $sourcePrefix = [IO.Path]::GetFullPath($source).TrimEnd('\') + '\'
    foreach ($item in $manifest.files) {
        $path = [IO.Path]::GetFullPath((Join-Path $source $item.path))
        if (-not $path.StartsWith($sourcePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Manifest path escapes skill directory.' }
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Missing file: $($item.path)" }
        if ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Linked source file is not supported.' }
        $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
        $inputStream = [IO.File]::OpenRead($path)
        try {
            $actualHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash($inputStream)).Replace('-', '')
        } finally {
            $inputStream.Dispose()
            $hashAlgorithm.Dispose()
        }
        if ($actualHash -ne $item.sha256) { throw "File hash mismatch: $($item.path)" }
    }
    # Validate every file before creating a destination. Stage beside the chosen
    # destination; a failure leaves a clearly named staging directory for diagnosis.
    $parent = Split-Path -Parent $target
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $stage = Join-Path $parent ('.timing-studio-install-' + [guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($stage) | Out-Null
    foreach ($item in $manifest.files) {
        $output = Join-Path $stage $item.path
        [IO.Directory]::CreateDirectory((Split-Path -Parent $output)) | Out-Null
        Copy-Item -LiteralPath (Join-Path $source $item.path) -Destination $output
    }
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $stage 'install-manifest.json')
    if (Test-Path -LiteralPath $target) { throw 'Destination appeared during install; refusing to replace it.' }
    # Both resolved paths are verified siblings under the explicit destination parent.
    if ([IO.Path]::GetFullPath((Split-Path -Parent $stage)) -ne [IO.Path]::GetFullPath($parent)) { throw 'Invalid staging path.' }
    [IO.Directory]::Move($stage, $target)
    Write-Output "Installed timing-studio to: $target"
} catch {
    Write-Error $_ -ErrorAction Continue
    exit 1
}
