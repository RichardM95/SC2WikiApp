param(
    [string]$Config,
    [string]$ProjectDir,
    [string]$HubRoot,
    [string]$ContentPack,
    [string]$GoExecutable,
    [string]$Output,
    [switch]$SkipFrontendBuild
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Config) { $Config = Join-Path $repoRoot 'config/local.json' }
$settings = @{}
if (Test-Path -LiteralPath $Config) {
    $configPath = (Resolve-Path -LiteralPath $Config).Path
    $values = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    foreach ($property in $values.PSObject.Properties) {
        if ($property.Value -isnot [string] -or -not $property.Value.Trim()) { throw "Invalid local path: $($property.Name)" }
        $path = $property.Value
        if (-not [IO.Path]::IsPathRooted($path)) { $path = Join-Path (Split-Path -Parent $configPath) $path }
        $settings[$property.Name] = [IO.Path]::GetFullPath($path)
    }
}
if (-not $ProjectDir) { $ProjectDir = $settings.projectDir }
if (-not $HubRoot) { $HubRoot = $settings.hubRoot }
if (-not $GoExecutable) { $GoExecutable = $settings.goExecutable }
if (-not $GoExecutable) { $command = Get-Command go -ErrorAction SilentlyContinue; if ($command) { $GoExecutable = $command.Source } }
if (-not $ProjectDir -or -not $HubRoot -or -not $GoExecutable) { throw 'Configure projectDir, hubRoot and goExecutable in config/local.json or pass explicit parameters.' }
if (-not $ContentPack) {
    $runtimeRoot = Join-Path $ProjectDir 'products/SCORE-wiki'
    $active = Get-Content -LiteralPath (Join-Path $runtimeRoot 'active.json') -Raw | ConvertFrom-Json
    $ContentPack = Join-Path $runtimeRoot "packs/$($active.current)"
}
if (-not $Output) { $Output = Join-Path $ProjectDir ("products/SCORE-wiki-build-" + (Get-Date -Format 'yyyyMMdd-HHmmss-fff')) }
if (Test-Path -LiteralPath $Output) { throw 'Output must be a fresh directory.' }
Push-Location $repoRoot
try {
    if (-not $SkipFrontendBuild) { & npm.cmd run build; if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' } }
    & python (Join-Path $PSScriptRoot 'build.py') --project-dir $ProjectDir --output $Output --content-pack $ContentPack --frontend-dist (Join-Path $repoRoot 'apps/web/dist') --launcher-dir (Join-Path $repoRoot 'apps/launcher') --go-exe $GoExecutable --default-config (Join-Path $repoRoot 'config/factions.default.json') --hub-root $HubRoot
    if ($LASTEXITCODE -ne 0) { throw 'Package assembly failed' }
    Write-Host "Complete package: $Output"
} finally { Pop-Location }
