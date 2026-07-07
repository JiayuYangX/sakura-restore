param(
    [Parameter(Mandatory=$true)]
    [string]$GhostDir
)

$ShellDir = Join-Path $GhostDir "shell\master"
$BalloonDir = Join-Path $GhostDir "balloon"
$ShellSurfaces = Join-Path $ShellDir "surfaces.txt"
$BalloonDesc = Join-Path $BalloonDir "descript.txt"

# ===== Shell =====
Write-Host "=== Shell ==="
$ShellPngs = Get-ChildItem "$ShellDir\surface*.png"

# surface2230 用蓝色
$BlueOne = $ShellPngs | Where-Object { $_.Name -eq "surface2230.png" }
$RedOnes = $ShellPngs | Where-Object { $_.Name -ne "surface2230.png" }

if ($BlueOne) {
    Write-Host "  处理 $($BlueOne.Name) (蓝 #0000FF)"
    magick $BlueOne.FullName -fuzz 5% -transparent "#0000FF" $BlueOne.FullName
}

$i = 0
foreach ($f in $RedOnes) {
    $i++
    Write-Host "  ($i/$($RedOnes.Count)) $($f.Name) (红 #FF0000)"
    magick $f.FullName -fuzz 5% -transparent "#FF0000" $f.FullName
}

# ===== Balloon =====
Write-Host "=== Balloon ==="
$BalloonPngs = Get-ChildItem "$BalloonDir\balloon*.png"

$j = 0
foreach ($f in $BalloonPngs) {
    $j++
    Write-Host "  ($j/$($BalloonPngs.Count)) $($f.Name) (灰 #808080)"
    magick $f.FullName -fuzz 5% -transparent "#808080" $f.FullName
}

Write-Host "完成"
