[CmdletBinding()]
param(
    [switch]$Apply,

    [string]$AwsProfile = $env:AWS_PROFILE,

    [string]$BucketName = "vmsenergy-web-prod-787140332697",

    [string]$DistributionId = "E3FWVCRXQ69DI6"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $Utf8NoBom
$global:OutputEncoding = $Utf8NoBom

foreach ($CommandName in @("git", "aws")) {
    if (-not (Get-Command $CommandName -ErrorAction SilentlyContinue)) {
        throw "No se encontró el comando requerido: $CommandName"
    }
}

$RepoRoot = (& git rev-parse --show-toplevel).Trim()

if ($LASTEXITCODE -ne 0 -or -not $RepoRoot) {
    throw "No fue posible identificar la raíz del repositorio."
}

$LocalBranch = (& git -C $RepoRoot branch --show-current).Trim()
$GitHubBranch = [Environment]::GetEnvironmentVariable("GITHUB_REF_NAME")

$EffectiveBranch = if ($GitHubBranch) {
    $GitHubBranch
}
else {
    $LocalBranch
}

if ($Apply -and $EffectiveBranch -ne "main") {
    throw "Despliegue bloqueado: -Apply solo se permite desde main. Rama actual: $EffectiveBranch"
}

$GitStatus = @(
    & git -C $RepoRoot status --porcelain --untracked-files=all
)

if ($Apply -and $GitStatus.Count -gt 0) {
    throw "Despliegue bloqueado: el repositorio contiene cambios sin commit."
}

$ExcludedTopLevels = @(
    ".github",
    ".claude",
    "infra",
    "scripts",
    "_dist",
    "_nucleo-wordpress",
    "assets - copia",
    "vms_web_backup"
)

$ExcludedExactPaths = @(
    ".gitignore",
    "assets/img/solar.zip",
    "Especialidades",
    "stand-rim-2026 3.html"
)

$ExcludedExtensions = @(
    ".md",
    ".csv"
)

function Test-DeployablePath {
    param(
        [Parameter(Mandatory)]
        [string]$RelativePath
    )

    $NormalizedPath = $RelativePath.Replace("\", "/")
    $TopLevel = ($NormalizedPath -split "/", 2)[0]
    $FileName = [System.IO.Path]::GetFileName($NormalizedPath)
    $Extension = [System.IO.Path]::GetExtension(
        $FileName
    ).ToLowerInvariant()

    if ($TopLevel -in $ExcludedTopLevels) {
        return $false
    }

    if ($NormalizedPath -in $ExcludedExactPaths) {
        return $false
    }

    if ($FileName -like "*.bak*") {
        return $false
    }

    if ($Extension -in $ExcludedExtensions) {
        return $false
    }

    return $true
}

$TrackedFiles = @(
    & git -C $RepoRoot -c core.quotepath=false ls-files
)

if ($LASTEXITCODE -ne 0) {
    throw "No fue posible obtener los archivos rastreados por Git."
}

$DeployableFiles = @(
    $TrackedFiles | Where-Object {
        Test-DeployablePath -RelativePath $_
    }
)

$ArtifactRoot = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ("vms-energy-web-" + [guid]::NewGuid().ToString("N"))

New-Item -ItemType Directory -Path $ArtifactRoot |
    Out-Null

try {
    [long]$TotalBytes = 0
    $Separator = [string][System.IO.Path]::DirectorySeparatorChar

    foreach ($RelativePath in $DeployableFiles) {
        $PlatformPath = $RelativePath.Replace("/", $Separator)
        $SourcePath = Join-Path $RepoRoot $PlatformPath
        $DestinationPath = Join-Path $ArtifactRoot $PlatformPath

        if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
            throw "No se encontró el archivo rastreado: $RelativePath"
        }

        $DestinationDirectory = Split-Path `
            $DestinationPath `
            -Parent

        New-Item `
            -ItemType Directory `
            -Force `
            -Path $DestinationDirectory |
            Out-Null

        Copy-Item `
            -LiteralPath $SourcePath `
            -Destination $DestinationPath

        $TotalBytes += (
            Get-Item -LiteralPath $SourcePath
        ).Length
    }

    Write-Host ""
    Write-Host "Rama: $EffectiveBranch"
    Write-Host "Archivos del sitio: $($DeployableFiles.Count)"
    Write-Host "Tamaño MiB: $([math]::Round($TotalBytes / 1MB, 2))"
    Write-Host "Bucket: $BucketName"
    Write-Host "Modo: $(if ($Apply) { 'APLICAR' } else { 'SIMULACION' })"
    Write-Host ""

    $AwsCommonArguments = @()

    if (-not [string]::IsNullOrWhiteSpace($AwsProfile)) {
        $AwsCommonArguments += @(
            "--profile",
            $AwsProfile
        )
    }

    $SyncArguments = @(
        "s3",
        "sync",
        $ArtifactRoot,
        "s3://$BucketName",
        "--delete",
        "--cache-control",
        "public,max-age=300,must-revalidate",
        "--sse",
        "AES256",
        "--no-progress"
    )

    if (-not $Apply) {
        $SyncArguments += "--dryrun"
    }

    $SyncArguments += $AwsCommonArguments

    & aws @SyncArguments

    if ($LASTEXITCODE -ne 0) {
        throw "La sincronización con S3 terminó con error."
    }

    if (-not $Apply) {
        Write-Host ""
        Write-Host "Simulación terminada. No se modificó AWS."
        return
    }

    $InvalidationArguments = @(
        "cloudfront",
        "create-invalidation",
        "--distribution-id",
        $DistributionId,
        "--paths",
        "/*",
        "--query",
        "Invalidation.Id",
        "--output",
        "text"
    ) + $AwsCommonArguments

    $InvalidationId = (
        & aws @InvalidationArguments
    ).Trim()

    if ($LASTEXITCODE -ne 0 -or -not $InvalidationId) {
        throw "No fue posible crear la invalidación de CloudFront."
    }

    $WaitArguments = @(
        "cloudfront",
        "wait",
        "invalidation-completed",
        "--distribution-id",
        $DistributionId,
        "--id",
        $InvalidationId
    ) + $AwsCommonArguments

    & aws @WaitArguments

    if ($LASTEXITCODE -ne 0) {
        throw "La espera de invalidación terminó con error."
    }

    Write-Host ""
    Write-Host "Despliegue completado."
    Write-Host "Invalidación: $InvalidationId"
}
finally {
    if (Test-Path -LiteralPath $ArtifactRoot) {
        Remove-Item `
            -LiteralPath $ArtifactRoot `
            -Recurse `
            -Force
    }
}

