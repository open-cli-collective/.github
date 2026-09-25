[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$renderer = Join-Path $PSScriptRoot 'render.ps1'
$root = Join-Path ([IO.Path]::GetTempPath()) "chocolatey-render-$([guid]::NewGuid())"
New-Item -ItemType Directory -Path $root | Out-Null

function New-Fixture {
    param(
        [string]$Name,
        [string]$InstallScript
    )
    $package = Join-Path $root "$Name/packaging/chocolatey"
    New-Item -ItemType Directory -Path (Join-Path $package 'tools') -Force | Out-Null
    Set-Content (Join-Path $package "$Name.nuspec") '<version>0.0.0</version>'
    Set-Content (Join-Path $package 'tools/chocolateyInstall.ps1') $InstallScript
    [pscustomobject]@{
        Name = $Name
        WorkingDirectory = Join-Path $root $Name
        PackageDirectory = 'packaging/chocolatey'
        Script = Join-Path $package 'tools/chocolateyInstall.ps1'
        Nuspec = Join-Path $package "$Name.nuspec"
        Output = Join-Path $root "$Name-output.txt"
    }
}

function Assert-Contains {
    param([string]$Text, [string]$Expected, [string]$Message)
    if (-not $Text.Contains($Expected)) {
        throw "${Message}: expected '$Expected'"
    }
}

function Assert-NotContains {
    param([string]$Text, [string]$Unexpected, [string]$Message)
    if ($Text.Contains($Unexpected)) {
        throw "${Message}: found '$Unexpected'"
    }
}

function Invoke-Render {
    param(
        [pscustomobject]$Fixture,
        [string]$Repository = 'open-cli-collective/google-cli',
        [string]$FinalTag = 'v2.0.123',
        [string]$X64Asset = 'google_windows_amd64.zip',
        [string]$Arm64Asset = 'google_windows_arm64.zip',
        [string]$X64Hash = ('a' * 64),
        [string]$Arm64Hash = ('b' * 64)
    )
    & $renderer `
        -WorkingDirectory $Fixture.WorkingDirectory `
        -PackageDirectory $Fixture.PackageDirectory `
        -PackageId $Fixture.Name `
        -Version '2.0.123' `
        -Repository $Repository `
        -FinalTag $FinalTag `
        -X64Asset $X64Asset `
        -Arm64Asset $Arm64Asset `
        -X64Hash $X64Hash `
        -Arm64Hash $Arm64Hash `
        -GitHubOutput $Fixture.Output
}

function Assert-RenderFails {
    param(
        [scriptblock]$Action,
        [string]$ExpectedMessage
    )
    $matched = $false
    try {
        & $Action
    } catch {
        $matched = $_.Exception.Message -match $ExpectedMessage
    }
    if (-not $matched) {
        throw "renderer should fail with '$ExpectedMessage'"
    }
}

try {
    $packages = @(
        @{ Name = 'google-readonly'; X64 = 'gro_v2.0.123_windows_amd64.zip'; ARM64 = 'gro_v2.0.123_windows_arm64.zip'; X64Hash = ('1' * 64); ARM64Hash = ('2' * 64) },
        @{ Name = 'google-readwrite'; X64 = 'grw_v2.0.123_windows_amd64.zip'; ARM64 = 'grw_v2.0.123_windows_arm64.zip'; X64Hash = ('3' * 64); ARM64Hash = ('4' * 64) }
    )
    foreach ($package in $packages) {
        $fixture = New-Fixture $package.Name @"
`$url64 = 'URL_AMD64_PLACEHOLDER'
`$urlArm64 = 'URL_ARM64_PLACEHOLDER'
`$checksum64 = 'CHECKSUM_AMD64_PLACEHOLDER'
`$checksumArm64 = 'CHECKSUM_ARM64_PLACEHOLDER'
"@
        Invoke-Render $fixture -X64Asset $package.X64 -Arm64Asset $package.ARM64 -X64Hash $package.X64Hash -Arm64Hash $package.ARM64Hash
        $rendered = Get-Content $fixture.Script -Raw
        $expectedBase = "https://github.com/open-cli-collective/google-cli/releases/download/v2.0.123/"
        Assert-Contains $rendered "$expectedBase$($package.X64)" "$($package.Name) AMD64 URL"
        Assert-Contains $rendered "$expectedBase$($package.ARM64)" "$($package.Name) ARM64 URL"
        Assert-Contains $rendered $package.X64Hash "$($package.Name) AMD64 hash"
        Assert-Contains $rendered $package.ARM64Hash "$($package.Name) ARM64 hash"
        Assert-NotContains $rendered 'URL_AMD64_PLACEHOLDER' "$($package.Name) AMD64 placeholder"
        Assert-NotContains $rendered 'URL_ARM64_PLACEHOLDER' "$($package.Name) ARM64 placeholder"
        Assert-Contains (Get-Content $fixture.Output -Raw) 'static-urls=true' "$($package.Name) static output"
        Assert-Contains (Get-Content $fixture.Nuspec -Raw) '<version>2.0.123</version>' "$($package.Name) version"
    }

    $mixed = New-Fixture 'mixed' @"
`$url64 = 'URL_AMD64_PLACEHOLDER'
`$urlArm64 = 'https://example.test/arm64.zip'
`$checksum64 = 'CHECKSUM_AMD64_PLACEHOLDER'
`$checksumArm64 = 'CHECKSUM_ARM64_PLACEHOLDER'
"@
    Assert-RenderFails { Invoke-Render $mixed } 'both URL_AMD64_PLACEHOLDER and URL_ARM64_PLACEHOLDER'

    $missing = New-Fixture 'missing-input' @"
`$url64 = 'URL_AMD64_PLACEHOLDER'
`$urlArm64 = 'URL_ARM64_PLACEHOLDER'
`$checksum64 = 'CHECKSUM_AMD64_PLACEHOLDER'
`$checksumArm64 = 'CHECKSUM_ARM64_PLACEHOLDER'
"@
    Assert-RenderFails { Invoke-Render $missing -Repository '' } 'repository, final tag, and both asset names'

    $legacy = New-Fixture 'legacy' @"
`$version = `$env:ChocolateyPackageVersion
`$url64 = "https://example.test/v`${version}/amd64.zip"
`$urlArm64 = "https://example.test/v`${version}/arm64.zip"
`$checksum64 = 'CHECKSUM_AMD64_PLACEHOLDER'
`$checksumArm64 = 'CHECKSUM_ARM64_PLACEHOLDER'
"@
    Invoke-Render $legacy -Repository '' -FinalTag '' -X64Asset '' -Arm64Asset ''
    $legacyRendered = Get-Content $legacy.Script -Raw
    Assert-Contains $legacyRendered 'ChocolateyPackageVersion' 'legacy runtime URL'
    Assert-Contains $legacyRendered ('a' * 64) 'legacy AMD64 hash'
    Assert-Contains $legacyRendered ('b' * 64) 'legacy ARM64 hash'
    Assert-Contains (Get-Content $legacy.Output -Raw) 'static-urls=false' 'legacy static output'

    Write-Output 'chocolatey renderer checks passed'
} finally {
    Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
}
