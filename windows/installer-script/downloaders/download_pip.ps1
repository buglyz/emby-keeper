# A script to download and install pip using get-pip.py
# Usage: .\download_pip.ps1 [-TargetDirectory <TargetDirectory>] [-NoMirror]
# Example: .\download_pip.ps1 -TargetDirectory C:\Python\3.11.9 -NoMirror

param(
    [Parameter()]
    [string]$TargetDirectory=(Get-Location).Path,
    [Parameter()]
    [switch]$NoMirror = $false
)

$PipUrl = "https://bootstrap.pypa.io/get-pip.py"
$PipFile = "$TargetDirectory\get-pip.py"
$PipExe = "$TargetDirectory\Scripts\pip.exe"

if (Test-Path $PipExe) {
    Write-Host "Pip already downloaded"
    exit 0
}

Write-Host "Downloading get-pip.py"
$Proxy = [System.Net.WebRequest]::GetSystemWebproxy()
$ProxyBypassed = $Proxy.IsBypassed($PipUrl)
if ($ProxyBypassed){
    Invoke-WebRequest -Uri $PipUrl -OutFile $PipFile
} else {
    $ProxyUrl = $Proxy.GetProxy($PipUrl)
    Invoke-WebRequest -Uri $PipUrl -OutFile $PipFile -Proxy $ProxyUrl -ProxyUseDefaultCredentials
}

Write-Host "Installing pip"
if ($NoMirror) {
    & $TargetDirectory\python.exe $PipFile --no-warn-script-location
} else {
    & $TargetDirectory\python.exe $PipFile --no-warn-script-location -i "https://mirrors.aliyun.com/pypi/simple"
}
$exitCode = $LASTEXITCODE

if ($exitCode -eq 0) {
    Write-Host "Done installing pip"
} else {
    Write-Host "Failed to install pip, exit code: $exitCode"
}
exit $exitCode
