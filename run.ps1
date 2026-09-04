# Theta Council - Windows launcher
# Usage:  .\run.ps1 preflight | once | loop | dashboard | test | mock
param([string]$Cmd = "once", [int]$Interval = 600)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = "python"
foreach ($c in @("python","py","python3")) {
  if (Get-Command $c -ErrorAction SilentlyContinue) { $py = $c; break }
}
switch ($Cmd) {
  "test"      { & $py -m unittest discover -s tests -v }
  "mock"      { & $py -m council once --mock }
  "loop"      { & $py -m council loop --interval $Interval }
  "dashboard" { & $py -m council dashboard }
  default     { & $py -m council $Cmd }
}
