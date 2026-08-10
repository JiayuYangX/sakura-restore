param(
    [string]$InputPath = (Join-Path $PSScriptRoot "shell\master1"),
    [string]$OutputPath = (Join-Path $PSScriptRoot "shell\master_4x")
)

New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
$files = Get-ChildItem $InputPath -Filter *.png
$i = 0
foreach ($f in $files) {
    $i++; $name = $f.Name
    $fullName = "$($f.BaseName).full.png"
    if (-not $name.EndsWith(".full.png") -and (Test-Path (Join-Path $InputPath $fullName))) {
        Write-Host "[$i/$($files.Count)] $name -> skip (use $fullName)"
        continue
    }
    Write-Host "[$i/$($files.Count)] $name"
    realesrgan-ncnn-vulkan.exe -i $f.FullName -o "$OutputPath\$name" -n realesrgan-x4plus-anime -s 4 -t 100 2>&1 | Out-Null
}
Write-Host "Done"
