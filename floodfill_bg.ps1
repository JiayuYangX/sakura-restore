param(
    [string]$InputPath = (Join-Path $PSScriptRoot "shell\master_mark"),
    [string]$OutputPath = (Join-Path $PSScriptRoot "shell\master_clean"),
    [string]$SourcePath = (Join-Path $PSScriptRoot "shell\master1"),
    [int]$FuzzPercent = 35
)

New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null

$map = @(
    @{ Base = "surface0000"; Expr = "surface0008_"; X = 107; Y = 62  },
    @{ Base = "surface0000"; Expr = "surface1000";  X = 118; Y = 78  },
    @{ Base = "surface0000"; Expr = "surface1001";  X = 118; Y = 78  },
    @{ Base = "surface0007"; Expr = "surface0009_"; X = 111; Y = 103 },
    @{ Base = "surface0007"; Expr = "surface2070";  X = 98;  Y = 50  },
    @{ Base = "surface0007"; Expr = "surface2071";  X = 98;  Y = 50  },
    @{ Base = "surface0020"; Expr = "surface0021_"; X = 114; Y = 64  },
    @{ Base = "surface0020"; Expr = "surface1200";  X = 117; Y = 66  },
    @{ Base = "surface0020"; Expr = "surface1201";  X = 117; Y = 66  }
)

$files = Get-ChildItem $InputPath -Filter *.png
$i = 0

foreach ($f in $files) {
    $i++; $name = $f.Name
    Write-Host "[$i/$($files.Count)] $name"

    if (Test-Path (Join-Path $SourcePath $name)) {
        $origW, $origH = (magick identify -format "%w %h" "$SourcePath\$name") -split ' '
    } else {
        $w4, $h4 = (magick identify -format "%w %h" $f.FullName) -split ' '
        $origW = [int]$w4 / 4; $origH = [int]$h4 / 4
    }

    $hasGreen = magick $f.FullName -fuzz 0% -fill white +opaque "#00FF00" -fill black +opaque white -format "%[mean]" info: 2>$null
    if (-not $hasGreen -or $hasGreen -eq "0") {
        Write-Host "  skip (no green dot)"
        Copy-Item $f.FullName "$OutputPath\$name"
        continue
    }

    magick $f.FullName -fuzz 0% -fill white +opaque "#00FF00" -fill black +opaque white -connected-components 4 "$OutputPath\_cc.png"

    $labels = @()
    $uniq = magick "$OutputPath\_cc.png" -unique-colors txt: 2>$null
    foreach ($line in $uniq) {
        if ($line -match '^\d+,\d+: \((\d+)\)' -and [int]$matches[1] -gt 0) {
            $labels += [int]$matches[1]
        }
    }

    $seeds = @()
    foreach ($label in $labels) {
        $coord = magick "$OutputPath\_cc.png" `( +clone -evaluate Set $label `) -compose Difference -composite -threshold 0% -negate -trim -format "%X %Y" info: 2>$null
        if ($coord -match '([+-]\d+) ([+-]\d+)' -and [int]$matches[1] -ge 0 -and [int]$matches[2] -ge 0) {
            $seeds += ,@([int]$matches[1], [int]$matches[2])
        }
    }

    if ($seeds.Count -eq 0) {
        Write-Host "  skip (no seed found)"
        Copy-Item $f.FullName "$OutputPath\$name"
        Remove-Item "$OutputPath\_cc.png"
        continue
    }

    # Step 1: Flood fill → transparent background
    $args1 = @($f.FullName, "-fill", "red", "-opaque", "#00FF00",
               "-fuzz", "${FuzzPercent}%", "-fill", "none")
    foreach ($s in $seeds) { $args1 += "-draw"; $args1 += "color $($s[0]),$($s[1]) floodfill" }
    $args1 += "$OutputPath\_ff.png"
    & "magick" $args1

    # Step 2: Edge ring → black, then Box down
    $w4 = [int]$origW * 4; $h4 = [int]$origH * 4
    if ($name.EndsWith(".full.png")) {
        $fullOut = "$OutputPath\_f.png"
    } else {
        $fullOut = "$OutputPath\$name"
    }
    magick "$OutputPath\_ff.png" -write mpr:ff +delete `
      mpr:ff -alpha extract -threshold 0% -morphology EdgeIn Cross -write mpr:edge +delete `
      mpr:ff `( +clone -alpha off -evaluate Set 0 -alpha on mpr:edge -compose CopyOpacity -composite `) -compose Over -composite `
      -filter Box -resize "${origW}x${origH}!" $fullOut

    # Step 3: cut composited expressions back to part size
    if ($name.EndsWith(".full.png")) {
        $expr = $name.Substring(0, $name.Length - ".full.png".Length)
        $m = $map | Where-Object { $_.Expr -eq $expr } | Select-Object -First 1
        if (-not $m) { Remove-Item "$OutputPath\_f.png"; Remove-Item "$OutputPath\_ff.png"; continue }
        $partW, $partH = (magick identify -format "%w %h" (Join-Path $SourcePath "$expr.png")) -split ' '
        $exprOut = "$OutputPath\$expr.png"
        if ($expr -eq "surface1000" -or $expr -eq "surface1001") {
            $cw = [int]$partW; $ch = [int]$partH
            $x1 = $cw - 11; $y1 = $ch - 10
            $mask = Join-Path $env:TEMP "corner_mask.png"
            magick -size "${cw}x${ch}" xc:none -fill white -draw "rectangle $x1,$y1 $cw,$ch" $mask
            magick "$OutputPath\_f.png" -crop "${partW}x${partH}+$($m.X)+$($m.Y)" +repage `
              $mask -compose DstOut -composite $exprOut
            Remove-Item $mask
        } else {
            magick "$OutputPath\_f.png" -crop "${partW}x${partH}+$($m.X)+$($m.Y)" +repage $exprOut
        }
        Write-Host "  cut back $expr.png ($partW x $partH)"
        Remove-Item "$OutputPath\_f.png"
    }

    Remove-Item "$OutputPath\_ff.png"

    Remove-Item "$OutputPath\_cc.png"
}

Write-Host "Done"
