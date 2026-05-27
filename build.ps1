# Build FFCapture — increments the build number, embeds version into exe, runs PyInstaller.

$versionFile = Join-Path $PSScriptRoot 'version.txt'
$resourceFile = Join-Path $PSScriptRoot '_version_info.txt'

# --- Increment build number -------------------------------------------------
$raw = (Get-Content $versionFile -Raw).Trim()
$parts = $raw -split '\.'
if ($parts.Count -ne 4) {
    Write-Error "version.txt must contain exactly 4 components (e.g. 1.0.0.0)"
    exit 1
}
$parts[3] = [string]([int]$parts[3] + 1)
$version = $parts -join '.'
Set-Content $versionFile "$version`n" -NoNewline
Write-Host "Version: $version"

# --- Write PyInstaller version resource -------------------------------------
$t = $parts  # alias for readability
@"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($($t[0]), $($t[1]), $($t[2]), $($t[3])),
    prodvers=($($t[0]), $($t[1]), $($t[2]), $($t[3])),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)
  ),
  kids=[
    StringFileInfo([StringTable(u'040904B0', [
      StringStruct(u'FileDescription', u'FFCapture'),
      StringStruct(u'FileVersion',     u'$version'),
      StringStruct(u'InternalName',    u'FFCapture'),
      StringStruct(u'OriginalFilename',u'FFCapture.exe'),
      StringStruct(u'ProductName',     u'FFCapture'),
      StringStruct(u'ProductVersion',  u'$version'),
    ])]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@ | Set-Content $resourceFile -Encoding UTF8

# --- Build ------------------------------------------------------------------
& "$PSScriptRoot\venv\Scripts\pyinstaller.exe" "$PSScriptRoot\ffcapture.spec"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Build complete: dist\FFCapture.exe  ($version)"
