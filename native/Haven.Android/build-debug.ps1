param(
    [Parameter(Mandatory=$true)][string]$SdkRoot,
    [Parameter(Mandatory=$true)][string]$JavaHome
)
$ErrorActionPreference = 'Stop'
$source = $PSScriptRoot
$platform = Join-Path $SdkRoot 'platforms\android-37.0\android.jar'
$buildTools = Join-Path $SdkRoot 'build-tools\37.0.0'
foreach ($path in @($platform, (Join-Path $buildTools 'aapt2.exe'), (Join-Path $buildTools 'd8.bat'), (Join-Path $buildTools 'zipalign.exe'), (Join-Path $buildTools 'apksigner.bat'), (Join-Path $JavaHome 'bin\javac.exe'), (Join-Path $JavaHome 'bin\keytool.exe'))) { if (-not (Test-Path -LiteralPath $path)) { throw "Missing pinned tool: $path" } }
$env:JAVA_HOME = $JavaHome
$env:Path = (Join-Path $JavaHome 'bin') + ';' + $env:Path
$out = Join-Path $source 'build'
New-Item -ItemType Directory -Force -Path $out | Out-Null
$stage = Join-Path $out ([guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage, (Join-Path $stage 'classes'), (Join-Path $stage 'dex') | Out-Null
function Run-Checked([string]$program, [string[]]$arguments) { & $program @arguments; if ($LASTEXITCODE -ne 0) { throw "$program failed ($LASTEXITCODE)" } }
try {
    $unsigned = Join-Path $stage 'unsigned.apk'; $aligned = Join-Path $stage 'aligned.apk'; $key = Join-Path $stage 'ephemeral-debug.jks'
    Run-Checked (Join-Path $buildTools 'aapt2.exe') @('link','-o',$unsigned,'-I',$platform,'--manifest',(Join-Path $source 'AndroidManifest.xml'))
    $sources = @(Get-ChildItem (Join-Path $source 'src') -Filter '*.java' -Recurse | ForEach-Object { $_.FullName })
    Run-Checked (Join-Path $JavaHome 'bin\javac.exe') (@('-encoding','UTF-8','--release','8','-classpath',$platform,'-d',(Join-Path $stage 'classes')) + $sources)
    $classes = @(Get-ChildItem (Join-Path $stage 'classes') -Filter '*.class' -Recurse | ForEach-Object { $_.FullName })
    Run-Checked (Join-Path $buildTools 'd8.bat') (@('--min-api','26','--lib',$platform,'--output',(Join-Path $stage 'dex')) + $classes)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::Open($unsigned, [System.IO.Compression.ZipArchiveMode]::Update)
    try { [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, (Join-Path $stage 'dex\classes.dex'), 'classes.dex') | Out-Null } finally { $zip.Dispose() }
    Run-Checked (Join-Path $buildTools 'zipalign.exe') @('-f','4',$unsigned,$aligned)
    # Disposable debug identity, never a release key. Deleted with the staging directory.
    Run-Checked (Join-Path $JavaHome 'bin\keytool.exe') @('-genkeypair','-keystore',$key,'-storepass','android','-keypass','android','-alias','androiddebugkey','-dname','CN=HAVEN Temporary Debug','-keyalg','RSA','-keysize','2048','-validity','30','-noprompt')
    $apk = Join-Path $out 'haven-debug.apk'
    Run-Checked (Join-Path $buildTools 'apksigner.bat') @('sign','--v4-signing-enabled','false','--ks',$key,'--ks-pass','pass:android','--key-pass','pass:android','--out',$apk,$aligned)
    Run-Checked (Join-Path $buildTools 'apksigner.bat') @('verify','--verbose',$apk)
    Write-Output "Debug APK: $apk"
    Write-Output 'Disposable debug key: deleted after build. Rebuilds will not upgrade one another.'
} finally {
    if (Test-Path -LiteralPath $stage) {
        $resolvedStage = [System.IO.Path]::GetFullPath($stage)
        $resolvedOutput = [System.IO.Path]::GetFullPath($out).TrimEnd('\') + '\'
        if (-not $resolvedStage.StartsWith($resolvedOutput, [System.StringComparison]::OrdinalIgnoreCase)) { throw 'Build staging escaped the Android output directory.' }
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
