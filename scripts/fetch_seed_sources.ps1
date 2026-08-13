param(
    [string]$Destination = (Join-Path $PSScriptRoot "..\data\raw")
)

$ErrorActionPreference = "Stop"
$destinationPath = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null

$downloads = @(
    @{
        Name = "commoncrawl-domain-vertices-2026-may-jun-jul.txt.gz"
        Url = "https://data.commoncrawl.org/projects/hyperlinkgraph/cc-main-2026-may-jun-jul/domain/cc-main-2026-may-jun-jul-domain-vertices.txt.gz"
    },
    @{
        Name = "majestic_million.csv"
        Url = "https://downloads.majestic.com/majestic_million.csv"
    },
    @{
        Name = "tranco-top-1m.csv.zip"
        Url = "https://tranco-list.eu/top-1m.csv.zip"
    },
    @{
        Name = "umbrella-top-1m.csv.zip"
        Url = "https://umbrella-static.s3-us-west-1.amazonaws.com/top-1m.csv.zip"
    }
)

foreach ($download in $downloads) {
    $target = Join-Path $destinationPath $download.Name
    & curl.exe -L --fail --retry 3 --continue-at - --output $target $download.Url
    if ($LASTEXITCODE -ne 0) {
        throw "Download failed: $($download.Url)"
    }
    Get-FileHash -Algorithm SHA256 -LiteralPath $target |
        Select-Object Path, Hash
}
