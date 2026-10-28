# Windows PowerShell 5.1 compatible. Keep this file ASCII so -File works without a BOM.
# Only the private runtime is installed; no PATH, registry, or global TEMP changes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:PythonVersion = '3.12.10'
$script:PythonArchive = 'python-3.12.10-embed-amd64.zip'
$script:PythonUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip'
# Matches the downloaded archive and messageDigest in its python.org .sigstore bundle.
$script:PythonSha256 = '4ACBED6DD1C744B0376E3B1CF57CE906F9DC9E95E68824584C8099A63025A3C3'

function Get-TimingStudioPythonInfo {
    param([string]$Executable, [string]$Prefix = '')
    if (-not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $null }
    # Windows Store aliases can open an installer when probed. They are not runtimes.
    if ($Executable -match '[\\/]Microsoft[\\/]WindowsApps[\\/]') { return $null }
    $probe = New-Object System.Diagnostics.Process
    $probe.StartInfo.FileName = $Executable
    $probe.StartInfo.Arguments = $Prefix + ' -I -X utf8 -c "import json,sys; print(json.dumps([list(sys.version_info[:3]),sys.executable]))"'
    $probe.StartInfo.UseShellExecute = $false
    $probe.StartInfo.CreateNoWindow = $true
    $probe.StartInfo.RedirectStandardOutput = $true
    $probe.StartInfo.RedirectStandardError = $true
    try {
        if (-not $probe.Start()) { return $null }
        $stdout = $probe.StandardOutput.ReadToEndAsync()
        $stderr = $probe.StandardError.ReadToEndAsync()
        if (-not $probe.WaitForExit(10000)) {
            $probe.Kill()
            return $null
        }
        if ($probe.ExitCode -ne 0) { return $null }
        $info = $stdout.Result | ConvertFrom-Json
        if ($info.Count -ne 2 -or $info[0].Count -ne 3) { return $null }
        if ([int]$info[0][0] -ne 3 -or [int]$info[0][1] -lt 12) { return $null }
        if (-not (Test-Path -LiteralPath $info[1] -PathType Leaf)) { return $null }
        return [PSCustomObject]@{
            Executable = [string]$info[1]
            Version = ($info[0] -join '.')
        }
    } catch {
        return $null
    } finally {
        $probe.Dispose()
    }
}

function Find-TimingStudioSystemPython {
    # Resolve applications only, never a same-name PowerShell function or script.
    foreach ($name in @('python.exe', 'python3.exe', 'py.exe')) {
        $candidates = @(Get-Command $name -CommandType Application -All -ErrorAction SilentlyContinue)
        foreach ($candidate in $candidates) {
            $prefix = ''
            if ($name -eq 'py.exe') { $prefix = '-3' }
            $info = Get-TimingStudioPythonInfo -Executable $candidate.Source -Prefix $prefix
            if ($null -ne $info) { return $info.Executable }
        }
    }
    return $null
}

function Install-TimingStudioPython {
    param([string]$RuntimeDir)
    if (Test-Path -LiteralPath $RuntimeDir) {
        throw "Runtime location already exists; nothing was replaced: $RuntimeDir"
    }
    if (-not [Environment]::Is64BitOperatingSystem) {
        throw 'Automatic setup needs 64-bit Windows. Set TIMING_STUDIO_PYTHON to Python 3.12 or newer.'
    }
    $runtimeParent = Split-Path -Parent $RuntimeDir
    [IO.Directory]::CreateDirectory($runtimeParent) | Out-Null
    $staging = Join-Path $runtimeParent ('.timing-studio-install-' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($staging) | Out-Null
    $archive = Join-Path $staging $script:PythonArchive
    $expanded = Join-Path $staging 'python'
    $oldSecurityProtocol = [Net.ServicePointManager]::SecurityProtocol
    try {
        [Console]::Error.WriteLine('Downloading official Python ' + $script:PythonVersion + ' (Windows x64)...')
        [Net.ServicePointManager]::SecurityProtocol = $oldSecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $script:PythonUrl -OutFile $archive -UseBasicParsing
        $hasher = [Security.Cryptography.SHA256]::Create()
        $archiveStream = [IO.File]::OpenRead($archive)
        try {
            $actualHash = [BitConverter]::ToString($hasher.ComputeHash($archiveStream)).Replace('-', '')
        } finally {
            $archiveStream.Dispose()
            $hasher.Dispose()
        }
        if ($actualHash -ne $script:PythonSha256) {
            throw 'Python archive SHA256 verification failed. Nothing was extracted or executed.'
        }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($archive, $expanded)
        # Keep the verified source archive and Python's original LICENSE.txt / ._pth.
        Move-Item -LiteralPath $archive -Destination (Join-Path $expanded $script:PythonArchive)
        $info = Get-TimingStudioPythonInfo -Executable (Join-Path $expanded 'python.exe')
        if ($null -eq $info -or $info.Version -ne $script:PythonVersion) {
            throw 'The verified Python runtime could not start. Check Windows compatibility and antivirus messages.'
        }
        # Directory.Move fails if another process or user created the destination.
        # It never merges into or overwrites an existing directory.
        [IO.Directory]::Move($expanded, $RuntimeDir)
        [Console]::Error.WriteLine('Private Python ready: ' + $RuntimeDir)
        return (Join-Path $RuntimeDir 'python.exe')
    } finally {
        [Net.ServicePointManager]::SecurityProtocol = $oldSecurityProtocol
        # This path was generated above. Resolve and check its parent before cleanup.
        $resolvedStage = [IO.Path]::GetFullPath($staging)
        $resolvedParent = [IO.Path]::GetFullPath($runtimeParent).TrimEnd('\', '/')
        if ((Split-Path -Parent $resolvedStage).TrimEnd('\', '/') -eq $resolvedParent -and
            (Split-Path -Leaf $resolvedStage) -match '^\.timing-studio-install-[0-9a-f]{32}$') {
            if (Test-Path -LiteralPath $resolvedStage) {
                Remove-Item -LiteralPath $resolvedStage -Recurse -Force
            }
        }
    }
}

function Select-TimingStudioPython {
    param([string]$RuntimeDir)
    if (-not [string]::IsNullOrWhiteSpace($env:TIMING_STUDIO_PYTHON)) {
        $requested = $env:TIMING_STUDIO_PYTHON
        $info = Get-TimingStudioPythonInfo -Executable $requested
        if ($null -eq $info) {
            throw "TIMING_STUDIO_PYTHON must be an executable path for Python 3.12 or newer: $requested"
        }
        return $info.Executable
    }
    if (Test-Path -LiteralPath $RuntimeDir) {
        $info = Get-TimingStudioPythonInfo -Executable (Join-Path $RuntimeDir 'python.exe')
        if ($null -eq $info) {
            throw "Existing runtime is unusable and was not changed: $RuntimeDir. Set TIMING_STUDIO_PYTHON or choose a new TIMING_STUDIO_RUNTIME_DIR."
        }
        return $info.Executable
    }
    $systemPython = Find-TimingStudioSystemPython
    if ($null -ne $systemPython) { return $systemPython }
    return (Install-TimingStudioPython -RuntimeDir $RuntimeDir)
}

function Invoke-TimingStudio {
    param([string[]]$Arguments)
    $entryPoint = Join-Path $PSScriptRoot 'timing_studio.py'
    if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
        throw 'Missing scripts/timing_studio.py. Extract or install the complete Timing Studio skill folder.'
    }
    $runtimeDir = $env:TIMING_STUDIO_RUNTIME_DIR
    if ([string]::IsNullOrWhiteSpace($runtimeDir)) {
        $runtimeDir = Join-Path (Split-Path -Parent $PSScriptRoot) '.runtime'
    }
    $runtimeDir = [IO.Path]::GetFullPath($runtimeDir)
    $python = Select-TimingStudioPython -RuntimeDir $runtimeDir
    # The wrapper adds its packaged runtime to sys.path, including in embedded Python.
    # UTF-8 applies to files and pipes; -B avoids bytecode writes into installed files.
    & $python -I -X utf8 -B $entryPoint @Arguments
    $script:TimingStudioExitCode = $LASTEXITCODE
}

# Dot-sourcing exposes functions for offline selection and failure-path tests.
if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-TimingStudio -Arguments $args
        exit $script:TimingStudioExitCode
    } catch {
        [Console]::Error.WriteLine('Timing Studio: ' + $_.Exception.Message)
        exit 1
    }
}
