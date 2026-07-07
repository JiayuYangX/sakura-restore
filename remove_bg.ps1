param(
    [Parameter(Mandatory=$true)]
    [string]$GhostDir
)

function Remove-Background {
    param([string]$Path, [string]$Color)
    magick $Path -fuzz 5% -transparent $Color $Path
}

function Remove-WhiteEdge {
    param([string]$Path)
    $d = Join-Path ([System.IO.Path]::GetTempPath()) "ew_$([System.IO.Path]::GetRandomFileName())"
    New-Item -ItemType Directory -Path $d -Force | Out-Null
    magick $Path -alpha extract "$d\alpha.png"
    magick "$d\alpha.png" -edge 1 "$d\edge.png"
    $edgePixels = magick "$d\edge.png" -format "%[fx:mean*w*h]" info:
    if ([double]$edgePixels -gt 0) {
        magick $Path -colorspace Gray -negate "$d\gray.png"
        magick "$d\alpha.png" "$d\gray.png" "$d\edge.png" -fx 'u[2]>0.5 ? u[1]*u[1]*u[1] : u[0]' "$d\newalpha.png"
        magick $Path "$d\newalpha.png" -compose CopyAlpha -composite $Path
    }
    Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
}

$ShellDir = Join-Path $GhostDir "shell\master"
$BalloonDir = Join-Path $GhostDir "balloon"

# ===== Shell =====
Write-Host "=== Shell ==="
$ShellPngs = Get-ChildItem "$ShellDir\surface*.png"

$BlueOne = $ShellPngs | Where-Object { $_.Name -eq "surface2230.png" }
$RedOnes = $ShellPngs | Where-Object { $_.Name -ne "surface2230.png" }

if ($BlueOne) {
    Write-Host "  处理 $($BlueOne.Name) (蓝 #0000FF)"
    Remove-Background $BlueOne.FullName "#0000FF"
}

$i = 0
$NoEdge = @("surface1000.png", "surface1001.png")
foreach ($f in $RedOnes) {
    $i++
    Write-Host "  ($i/$($RedOnes.Count)) $($f.Name) (红 #FF0000)"
    Remove-Background $f.FullName "#FF0000"
    if ($f.Name -notin $NoEdge) {
        Remove-WhiteEdge $f.FullName
    }
}

# ===== Balloon =====
Write-Host "=== Balloon ==="
$BalloonPngs = Get-ChildItem "$BalloonDir\balloon*.png"

$j = 0
foreach ($f in $BalloonPngs) {
    $j++
    Write-Host "  ($j/$($BalloonPngs.Count)) $($f.Name) (灰 #808080)"
    Remove-Background $f.FullName "#808080"
}

Write-Host "完成"
