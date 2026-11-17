param([string]$BuildOverride = '')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskId = [guid]::NewGuid().ToString('N')
$taskProject = "trainer-backup-$($taskId.Substring(0,8))"
$taskSource = "trainer_test_backup_${taskId}_source"
$taskTarget = "trainer_test_backup_${taskId}_restored"
$taskDirectory = Join-Path $taskRoot ".tools/backup-$taskId"
New-Item -ItemType Directory -Path $taskDirectory | Out-Null
$taskCompose = @('compose', '-p', $taskProject, '-f', 'compose.test.yml')
if ($BuildOverride) { $taskCompose += @('-f', $BuildOverride) }
function Invoke-TaskDocker {
    & docker @args
    if ($LASTEXITCODE -ne 0) { throw "Docker terminó con código $LASTEXITCODE" }
}
try {
    Invoke-TaskDocker @taskCompose build tests
    Invoke-TaskDocker @taskCompose up -d --wait mongodb-test
    Invoke-TaskDocker @taskCompose run --rm --no-deps -T -v "${taskDirectory}:/backup-check" tests python backend/scripts/backup_restore_check.py prepare $taskSource $taskTarget /backup-check/state.json
    # Solo la instancia MongoDB de este proyecto temporal. Variables del contenedor.
    $taskDump = 'mongodump --host localhost --username "$MONGO_APP_USER" --password "$MONGO_APP_PASSWORD" --authenticationDatabase admin --db "$1" --archive=/tmp/backup.archive.gz --gzip'
    Invoke-TaskDocker @taskCompose exec -T mongodb-test sh -c $taskDump sh $taskSource
    Invoke-TaskDocker @taskCompose cp mongodb-test:/tmp/backup.archive.gz (Join-Path $taskDirectory 'backup.archive.gz')
    $taskRestore = 'mongorestore --host localhost --username "$MONGO_APP_USER" --password "$MONGO_APP_PASSWORD" --authenticationDatabase admin --archive=/tmp/backup.archive.gz --gzip --nsInclude "$1.*" --nsFrom "$1.*" --nsTo "$2.*" --stopOnError'
    Invoke-TaskDocker @taskCompose exec -T mongodb-test sh -c $taskRestore sh $taskSource $taskTarget
    Invoke-TaskDocker @taskCompose run --rm --no-deps -T -v "${taskDirectory}:/backup-check" tests python backend/scripts/backup_restore_check.py verify $taskSource $taskTarget /backup-check/state.json
    Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $taskDirectory 'backup.archive.gz') | Select-Object Algorithm, Hash
    Write-Output "Ensayo correcto. Artefactos privados de prueba en $taskDirectory"
} finally {
    Invoke-TaskDocker @taskCompose down --remove-orphans
}
