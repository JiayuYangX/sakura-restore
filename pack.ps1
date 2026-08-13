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

$s2230 = Join-Path $MasterSrc "surface2230.png"
$tmp4x = Join-Path $env:TEMP "s2230_4x.png"
$tmpClean = Join-Path $env:TEMP "s2230_clean.png"
$tmpNoyellow = Join-Path $env:TEMP "s2230_noyellow.png"
realesrgan-ncnn-vulkan.exe -i $s2230 -o $tmp4x -n realesrgan-x4plus-anime -s 4 -t 100 2>&1 | Out-Null
magick $tmp4x -fuzz 40% -fill none -draw "color 0,0 floodfill" -fill black -fuzz 55% -opaque "#0000FF" $tmpClean
magick $tmpClean -fx "b<g?#FFFFFF:u" $tmpNoyellow
$w, $h = (magick identify -format "%w %h" $s2230) -split ' '
magick $tmpNoyellow -filter Box -resize "${w}x${h}!" (Join-Path $OutShell "surface2230.png")
Remove-Item $tmp4x, $tmpClean, $tmpNoyellow
Write-Host "shell\master surface2230.png (upscaled floodfill->black, yellow->white)"

$balloons = Get-ChildItem $BalloonSrc -Filter *.png
$j = 0
foreach ($f in $balloons) {
    $j++
    if ($f.Name -like "balloon*.png" -or $f.Name -eq "thumbnail.png") {
        magick $f.FullName -fuzz 0 -transparent "#808080" (Join-Path $OutBalloon $f.Name)
        Write-Host "[$j/$($balloons.Count)] balloon $($f.Name) (key #808080)"
    } else {
        Copy-Item $f.FullName (Join-Path $OutBalloon $f.Name)
        Write-Host "[$j/$($balloons.Count)] balloon $($f.Name) (copy)"
    }
}

Write-Host "Done"