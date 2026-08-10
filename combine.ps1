param(
    [string]$ImageDir = (Join-Path $PSScriptRoot "shell\master1")
)

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

$tmpCut = Join-Path $env:TEMP "comp_part_cut.png"

foreach ($m in $map) {
    $basePath = Join-Path $ImageDir "$($m.Base).png"
    $exprPath = Join-Path $ImageDir "$($m.Expr).png"
    $outPath  = Join-Path $ImageDir "$($m.Expr).full.png"

    if (-not (Test-Path -LiteralPath $basePath)) { Write-Host "skip $($m.Base) (not found)"; continue }
    if (-not (Test-Path -LiteralPath $exprPath)) { Write-Host "skip $($m.Expr) (not found)"; continue }

    & magick $exprPath -fuzz 0 -transparent "#FF0000" $tmpCut
    $cutSrc = $tmpCut

    & magick $basePath $cutSrc -geometry "+$($m.X)+$($m.Y)" -composite $outPath
    if (-not $?) { Write-Host "ERROR on $($m.Expr)" }
}

Remove-Item -LiteralPath $tmpCut -ErrorAction SilentlyContinue
Write-Host "Done"