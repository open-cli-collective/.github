[CmdletBinding()]
param(
    [string]$WorkingDirectory = ".",
    [string]$PackageDirectory,
    [string]$PackageId,
    [string]$Version,
    [string]$Repository,
    [string]$FinalTag,
    [string]$X64Asset,
    [string]$Arm64Asset,
    [string]$X64Hash,
    [string]$Arm64Hash,
    [string]$GitHubOutput = ""
)

$ErrorActionPreference = 'Stop'

$requiredInputs = @{
    WorkingDirectory = $WorkingDirectory
    PackageDirectory = $PackageDirectory
    PackageId = $PackageId
    Version = $Version
    X64Hash = $X64Hash
    Arm64Hash = $Arm64Hash
}
foreach ($required in $requiredInputs.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($required.Value)) {
        throw "$($required.Key) is required"
    }
}

$nuspec = Join-Path $WorkingDirectory (Join-Path $PackageDirectory "$PackageId.nuspec")
$script = Join-Path $WorkingDirectory (Join-Path $PackageDirectory "tools/chocolateyInstall.ps1")

$content = Get-Content $nuspec -Raw
if ($content -notmatch '<version>0\.0\.0</version>') {
    throw "nuspec $nuspec has no <version>0.0.0</version> placeholder"
}
$content -replace '<version>0\.0\.0</version>', "<version>$Version</version>" | Set-Content $nuspec

$content = Get-Content $script -Raw
if ($content -notmatch 'CHECKSUM_AMD64_PLACEHOLDER') {
    throw "$script has no CHECKSUM_AMD64_PLACEHOLDER"
}
if ($content -notmatch 'CHECKSUM_ARM64_PLACEHOLDER') {
    throw "$script has no CHECKSUM_ARM64_PLACEHOLDER"
}

# Both URL placeholders opt a package into static rendering. Legacy templates
# omit both and keep their existing runtime URL path during migration.
$hasAmd64Url = $content.Contains('URL_AMD64_PLACEHOLDER')
$hasArm64Url = $content.Contains('URL_ARM64_PLACEHOLDER')
if ($hasAmd64Url -xor $hasArm64Url) {
    throw "$script must contain both URL_AMD64_PLACEHOLDER and URL_ARM64_PLACEHOLDER"
}
$staticUrls = $hasAmd64Url -and $hasArm64Url
if ($staticUrls) {
    if ([string]::IsNullOrWhiteSpace($Repository) -or [string]::IsNullOrWhiteSpace($FinalTag) -or [string]::IsNullOrWhiteSpace($X64Asset) -or [string]::IsNullOrWhiteSpace($Arm64Asset)) {
        throw "$script cannot render static URLs without repository, final tag, and both asset names"
    }
    if ($content -match '(?i)(?:ChocolateyPackageVersion|\$\{\s*version\s*\})') {
        throw "$script retains a runtime package-version expression; migrated packages require literal release URLs"
    }
    $x64Url = "https://github.com/$Repository/releases/download/$FinalTag/$X64Asset"
    $arm64Url = "https://github.com/$Repository/releases/download/$FinalTag/$Arm64Asset"
    $content = $content.Replace('URL_AMD64_PLACEHOLDER', $x64Url)
    $content = $content.Replace('URL_ARM64_PLACEHOLDER', $arm64Url)
}

$content = $content -replace 'CHECKSUM_AMD64_PLACEHOLDER', $X64Hash
$content = $content -replace 'CHECKSUM_ARM64_PLACEHOLDER', $Arm64Hash
if ($staticUrls -and $content -match 'URL_(?:AMD64|ARM64)_PLACEHOLDER|CHECKSUM_(?:AMD64|ARM64)_PLACEHOLDER|(?i:ChocolateyPackageVersion|\$\{\s*version\s*\})') {
    throw "$script still contains a URL/checksum placeholder or runtime package-version expression after rendering"
}
Set-Content $script $content

$staticOutput = $staticUrls.ToString().ToLowerInvariant()
if (-not [string]::IsNullOrWhiteSpace($GitHubOutput)) {
    Add-Content -Path $GitHubOutput -Value "static-urls=$staticOutput"
}
Write-Output "static-urls=$staticOutput"
