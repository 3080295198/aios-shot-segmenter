param(
    [Parameter(Mandatory = $false)]
    [string]$InputPath = ".\input-videos",

    [Parameter(Mandatory = $false)]
    [string]$OutputPath = ".\shot-results",

    [Parameter(Mandatory = $false)]
    [switch]$Overwrite
)

$ErrorActionPreference = "Stop"
$arguments = @("$PSScriptRoot\segment_videos.py", $InputPath, "--output", $OutputPath)
if ($Overwrite) {
    $arguments += "--overwrite"
}
python @arguments
exit $LASTEXITCODE
