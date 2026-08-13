param(
    [string]$MasterCleanSrc = (Join-Path $PSScriptRoot "shell\master_clean"),
    [string]$MasterSrc = (Join-Path $PSScriptRoot "shell\master"),
    [string]$BalloonSrc = (Join-Path $PSScriptRoot "balloon"),
    [string]$OutShell = (Join-Path $PSScriptRoot "output\shell\master"),
    [string]$OutBalloon = (Join-Path $PSScriptRoot "output\balloon")
)

New-Item -ItemType Directory -Path $OutShell -Force | Out-Null
New-Item -ItemType Directory -Path $OutBalloon -Force | Out-Null

$files = Get-ChildItem $MasterCleanSrc -Filter *.png
$i = 0
foreach ($f in $files) {
    $i++
    Copy-Item $f.FullName (Join-Path $OutShell $f.Name)
    Write-Host "[$i/$($files.Count)] shell\master $($f.Name)"
}

magick (Join-Path $MasterSrc "surface2230.png") -fuzz 0 -transparent "#0000FF" (Join-Path $OutShell "surface2230.png")
Write-Host "shell\master surface2230.png (key #0000FF)"

$balloons = Get-ChildItem $BalloonSrc -Filter *.png
$j = 0
foreach ($f in $balloons) {
    $j++
    magick $f.FullName -fuzz 0 -transparent "#808080" (Join-Path $OutBalloon $f.Name)
    Write-Host "[$j/$($balloons.Count)] balloon $($f.Name) (key #808080)"
}

Write-Host "Done"