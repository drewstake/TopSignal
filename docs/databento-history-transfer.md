# Transfer saved Databento history to another Windows laptop

The MNQ OHLCV-1m download is saved in the app's local data directory:

```text
backend\storage\databento\sources\GLBX-20260904-PSANB6M5GT\GLBX-20260904-PSANB6M5GT.zip
```

Keep this original ZIP. It contains 3,843,155 OHLCV records from May 5, 2019
through July 10, 2026, including outright contracts and spreads. It is enough
to transfer the downloaded candles without downloading them from Databento
again. Definition data is still required before building replay history.

History files are excluded from Git and are not stored in the application
database. A Git clone, code update, or database restore does not transfer them.
Copy the portable bundle separately when setting up a different device.

## Portable bundle

On the original laptop, the transfer bundle is:

```text
C:\Users\drews\Downloads\TopSignal-MNQ-OHLCV-1m-portable-20260904.zip
```

It contains the original Databento ZIP, validation report, contract inventory,
SHA-256 checksums, and these instructions. It contains no app configuration,
database dump, or operator credentials. The bundled validation report describes
the original validation on the first laptop; its recorded absolute paths are
not settings to apply to the new laptop.

1. Copy the portable bundle to the second laptop using a USB drive or your
   chosen private file transfer method. Keep a copy off the original laptop.
2. Extract the portable bundle into a new folder. Leave the Databento ZIP
   inside it zipped.
3. Copy the original Databento ZIP into the second app's data directory using
   the commands below. Change the first two paths to match that laptop.

```powershell
$bundleDirectory = 'C:\Users\YOUR_USER\Downloads\TopSignal-MNQ-OHLCV-1m-portable-20260904'
$repositoryDirectory = 'C:\Users\YOUR_USER\Development\TopSignal'
$sourceArchive = Join-Path $bundleDirectory 'GLBX-20260904-PSANB6M5GT.zip'
$expectedSha256 = '1872f93fd37c59b35ca7f72f972c3644a5d1b91301ff3b46f78d9c2e4925a5d1'
if ((Get-FileHash -LiteralPath $sourceArchive -Algorithm SHA256).Hash -ne $expectedSha256) {
    throw 'The transferred OHLCV archive failed its checksum. Copy it again.'
}
$historyDirectory = Join-Path $repositoryDirectory 'backend\storage\databento\sources\GLBX-20260904-PSANB6M5GT'
[System.IO.Directory]::CreateDirectory($historyDirectory) | Out-Null
$savedArchive = Join-Path $historyDirectory 'GLBX-20260904-PSANB6M5GT.zip'
if (-not (Test-Path -LiteralPath $savedArchive)) {
    [System.IO.File]::Copy($sourceArchive, $savedArchive, $false)
}
if ((Get-FileHash -LiteralPath $savedArchive -Algorithm SHA256).Hash -ne $expectedSha256) {
    throw 'The destination contains a different or damaged archive. Nothing was overwritten.'
}
Write-Output "Verified OHLCV history saved at $savedArchive"
```

The copy operation is idempotent and refuses to overwrite an existing file.
For the production layout in `windows-24x7-operations.md`, use
`C:\ProgramData\TopSignal\runtime\data\databento\sources\GLBX-20260904-PSANB6M5GT`
as `$historyDirectory` instead. Keep the source archives at their final paths
before building the replay cache.

## Finish the replay import when Definition is available

Place the matching MNQ Definition ZIP beside the saved source archive. In a
PowerShell window at the repository root, set these paths and build locally:

```powershell
$ohlcvArchive = 'C:\FULL\PATH\TO\GLBX-20260904-PSANB6M5GT.zip'
$definitionArchive = 'C:\FULL\PATH\TO\YOUR-MNQ-DEFINITION.zip'
$cacheDirectory = Join-Path (Get-Location) 'backend\storage\databento'
& backend\.venv\Scripts\python.exe backend\tools\build_databento_cache.py `
    $ohlcvArchive $definitionArchive --cache-dir $cacheDirectory --json
if ($LASTEXITCODE -ne 0) { throw 'Replay import failed; keep execution disabled.' }
```

For production, set `$cacheDirectory` to the same absolute path configured as
`TOPSIGNAL_DATABENTO_CACHE_DIR` in the backend environment. The default build
creates all supported timeframes. It reads local files and does not need a
Databento account or API key, contact the broker, or arm a bot.

Rebuild from the original ZIPs after moving to another laptop or changing the
source paths. Copying a compiled cache alone is insufficient: its integrity
manifest records the source archive paths and identities on the machine where
it was built. Keep both original ZIPs as durable inputs. Do not remove source
archives after building.

This bundle includes MNQ OHLCV only. It does not include Definition, MES
benchmark history, or candles after July 10, 2026. Databento's condition report
flags 16 dates within the candle range as degraded; structural validation does
not establish exchange-feed completeness. Importing this bundle does not
certify live trading or enable replay before the remaining data is available.
