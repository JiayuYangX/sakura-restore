param(
    [string]$InputPath = "C:\Users\JiaYu\AppData\Local\Programs\SSP\ghost\first2\shell\master",
    [string]$OutputPath = "C:\Users\JiaYu\AppData\Local\Programs\SSP\ghost\first2\shell\master_4x"
)

New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
$files = Get-ChildItem $InputPath -Filter *.png
$i = 0
foreach ($f in $files) {
    $i++; Write-Host "[$i/$($files.Count)] $($f.Name)"
    realesrgan-ncnn-vulkan.exe -i $f.FullName -o "$OutputPath\$($f.Name)" -n realesrgan-x4plus-anime -s 4 -t 100 2>&1 | Out-Null
}
Write-Host "Done"
