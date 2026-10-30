param([Parameter(Mandatory=$true)][string]$JavaHome)
$ErrorActionPreference='Stop'
$stage=Join-Path $PSScriptRoot ('test-build-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
  & (Join-Path $JavaHome 'bin\javac.exe') -encoding UTF-8 -d $stage (Join-Path $PSScriptRoot 'src\org\thehiddencanopy\haven\Outcome.java') (Join-Path $PSScriptRoot 'src\org\thehiddencanopy\haven\PhonePresentation.java') (Join-Path $PSScriptRoot 'src\org\thehiddencanopy\haven\ProductRules.java') (Join-Path $PSScriptRoot 'src\org\thehiddencanopy\haven\ThemeCatalog.java') (Join-Path $PSScriptRoot 'src\org\thehiddencanopy\haven\SessionRules.java') (Join-Path $PSScriptRoot 'tests\org\thehiddencanopy\haven\OutcomeTest.java')
  if ($LASTEXITCODE -ne 0) {throw 'javac failed'}
  & (Join-Path $JavaHome 'bin\java.exe') -cp $stage org.thehiddencanopy.haven.OutcomeTest
  if ($LASTEXITCODE -ne 0) {throw 'outcome tests failed'}
} finally {
  if (Test-Path -LiteralPath $stage) {
    $resolvedStage=[System.IO.Path]::GetFullPath($stage)
    $resolvedRoot=[System.IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    if (-not $resolvedStage.StartsWith($resolvedRoot,[System.StringComparison]::OrdinalIgnoreCase)) {throw 'Test staging escaped the Android source directory.'}
    Remove-Item -LiteralPath $resolvedStage -Recurse -Force
  }
}
