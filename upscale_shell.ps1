param(
    [string]$InputPath = (Join-Path $PSScriptRoot "shell\master1"),
    [string]$OutputPath = (Join-Path $PSScriptRoot "shell\master_4x")
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

$tmpCut = Join-Path $env:TEMP "combine_cut.png"

# ---- Step 1: combine all expression parts onto base images ----
for ($i = 0; $i -lt $map.Count; $i++) {
    $m = $map[$i]
    $basePath = Join-Path $InputPath "$($m.Base).png"
    $exprPath = Join-Path $InputPath "$($m.Expr).png"
    $fullPath = Join-Path $InputPath "$($m.Expr).full.png"
    if (-not (Test-Path -LiteralPath $basePath)) { Write-Host "[$($i+1)/$($map.Count)] combine skip $($m.Expr) (base missing)"; continue }
    if (-not (Test-Path -LiteralPath $exprPath)) { Write-Host "[$($i+1)/$($map.Count)] combine skip $($m.Expr) (expr missing)"; continue }

    & magick $exprPath -fuzz 0 -transparent "#FF0000" $tmpCut
    if (-not $?) { Write-Host "[$($i+1)/$($map.Count)] $($m.Expr) ERROR cut"; continue }
    & magick $basePath $tmpCut -geometry "+$($m.X)+$($m.Y)" -composite $fullPath
    if (-not $?) { Write-Host "[$($i+1)/$($map.Count)] $($m.Expr) ERROR combine"; continue }

    Write-Host "[$($i+1)/$($map.Count)] combine $($m.Base)+$($m.Expr) -> $($m.Expr).full.png"
}
Remove-Item -LiteralPath $tmpCut -ErrorAction SilentlyContinue
Write-Host "combine done"

# ---- Step 2: upscale all files in one loop ----
$files = Get-ChildItem $InputPath -Filter *.png | Where-Object {
    $n = $_.Name
    if ($n.EndsWith(".full.png")) { return $true }
    return -not (Test-Path (Join-Path $InputPath "$([IO.Path]::GetFileNameWithoutExtension($n)).full.png"))
}
$j = 0
foreach ($f in $files) {
    $j++
    Write-Host "[$j/$($files.Count)] $($f.Name)"
    realesrgan-ncnn-vulkan.exe -i $f.FullName -o (Join-Path $OutputPath $f.Name) -n realesrgan-x4plus-anime -s 4 -t 100 2>&1 | Out-Null
}

# ---- cleanup: remove temp .full.png from source dir ----
Get-ChildItem $InputPath -Filter *.full.png | ForEach-Object { Remove-Item -LiteralPath $_.FullName }
Write-Host "Done"