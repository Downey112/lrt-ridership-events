# Full pipeline: download -> analyse -> export for Power BI -> build PBIP -> validate -> tests
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
foreach ($s in "00_download.py", "01_analyse.py", "02_export_bi.py", "03_build_pbip.py", "check_pbir.py") {
    & $py (Join-Path $PSScriptRoot "scripts\$s")
    if ($LASTEXITCODE -ne 0) { throw "$s failed" }
}
& $py -m pytest -q (Join-Path $PSScriptRoot "tests")
if ($LASTEXITCODE -ne 0) { throw "tests failed" }
