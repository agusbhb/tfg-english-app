param(
    [ValidatePattern('^[a-z0-9][a-z0-9_-]+$')][string]$Project = 'trainer-schema3',
    [string]$SourceEnv = '.env.schema3',
    [string]$DestinationEnv = '.env'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot

function Invoke-ReadDocker {
    $taskResult = & docker @args
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo inspeccionar Docker. Inicia Docker Desktop.' }
    return $taskResult
}
function Read-EnvValues([string]$Text) {
    $taskValues = @{}
    foreach ($taskLine in ($Text -split '\r?\n')) {
        if ($taskLine -match '^([A-Z_][A-Z0-9_]*)=(.*)$') {
            $taskName = $Matches[1]
            $taskValue = $Matches[2].Trim()
            if ($taskValue -match '^(["''])(.*)\1$') { $taskValue = $Matches[2] }
            $taskValues[$taskName] = $taskValue
        }
    }
    return $taskValues
}
function Set-EnvValue([string]$Text, [string]$Name, [string]$Value) {
    $taskPattern = '(?m)^' + [regex]::Escape($Name) + '=.*$'
    if ([regex]::IsMatch($Text, $taskPattern)) {
        return [regex]::Replace($Text, $taskPattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($match) "$Name=$Value" })
    }
    return $Text.TrimEnd() + [Environment]::NewLine + "$Name=$Value" + [Environment]::NewLine
}

$taskSource = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $SourceEnv))
$taskDestination = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $DestinationEnv))
foreach ($taskPath in @($taskSource, $taskDestination)) {
    if (-not $taskPath.StartsWith($taskRoot + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Los archivos de entorno deben permanecer en este workspace.'
    }
}
if ($taskSource -eq $taskDestination -or [System.IO.Path]::GetFileName($taskDestination) -in @('.env.schema2', '.env.schema3')) {
    throw 'No se permite sobrescribir los archivos schema2/schema3.'
}
$taskText = [System.IO.File]::ReadAllText($taskSource)
$taskValues = Read-EnvValues $taskText
$taskMongoIds = @(Invoke-ReadDocker ps -a --filter "label=com.docker.compose.project=$Project" --filter 'label=com.docker.compose.service=mongodb' --format '{{.ID}}')
$taskBackendIds = @(Invoke-ReadDocker ps -a --filter "label=com.docker.compose.project=$Project" --filter 'label=com.docker.compose.service=backend' --format '{{.ID}}')
if ($taskMongoIds.Count -ne 1 -or $taskBackendIds.Count -ne 1) { throw 'Se requiere un único MongoDB y backend de fase 3.' }
$taskMongo = (Invoke-ReadDocker inspect $taskMongoIds[0] | ConvertFrom-Json)[0]
$taskBackend = (Invoke-ReadDocker inspect $taskBackendIds[0] | ConvertFrom-Json)[0]
$taskMounts = @($taskMongo.Mounts | Where-Object { $_.Destination -eq '/data/db' -and $_.Type -eq 'volume' })
if ($taskMounts.Count -ne 1) { throw 'No se encontró el volumen nombrado de datos; no se ha cambiado .env.' }
$taskContainerValues = Read-EnvValues ($taskMongo.Config.Env -join "`n")
$taskBackendValues = Read-EnvValues ($taskBackend.Config.Env -join "`n")
if ($taskContainerValues['MONGO_REPLICA_SET'] -ne 'trainer-local' -or $taskContainerValues['MONGO_REPLICA_HOST'] -ne 'mongodb:27017') {
    throw 'El contenedor existente no tiene la configuración de réplica esperada.'
}
foreach ($taskPair in @(
    @('MONGO_ROOT_USER', 'MONGO_INITDB_ROOT_USERNAME'),
    @('MONGO_ROOT_PASSWORD', 'MONGO_INITDB_ROOT_PASSWORD'),
    @('MONGO_APP_USER', 'MONGO_APP_USER'), @('MONGO_APP_PASSWORD', 'MONGO_APP_PASSWORD'),
    @('MONGODB_DB', 'MONGODB_DB')
)) {
    if (-not $taskValues.ContainsKey($taskPair[0]) -or $taskValues[$taskPair[0]] -cne $taskContainerValues[$taskPair[1]]) {
        throw "La configuración de $($taskPair[0]) no coincide con fase 3; no se muestran valores."
    }
}
foreach ($taskName in @('MONGODB_DB', 'JWT_SECRET')) {
    if ($taskValues[$taskName] -cne $taskBackendValues[$taskName]) { throw "$taskName no coincide con el backend de fase 3." }
}
foreach ($taskName in @('COMPOSE_PROJECT_NAME', 'MONGODB_VOLUME_NAME', 'MONGODB_DB', 'BANK_REFILL_ENABLED')) {
    $taskExpected = switch ($taskName) {
        'COMPOSE_PROJECT_NAME' { $Project }
        'MONGODB_VOLUME_NAME' { $taskMounts[0].Name }
        'MONGODB_DB' { $taskValues['MONGODB_DB'] }
        'BANK_REFILL_ENABLED' { 'false' }
    }
    $taskShellValue = [Environment]::GetEnvironmentVariable($taskName)
    if ($taskShellValue -and $taskShellValue -ne $taskExpected) { throw "Elimina la variable de shell $taskName antes de adoptar .env." }
}
$taskNewValues = @{
    COMPOSE_PROJECT_NAME = $Project
    COMPOSE_FILE = 'docker-compose.yml'
    MONGODB_VOLUME_NAME = $taskMounts[0].Name
    MONGODB_VOLUME_EXTERNAL = 'true'
    BANK_REFILL_ENABLED = 'false'
    BANK_REFILL_THRESHOLD = '20'
    BANK_REFILL_BATCH_SIZE = '30'
    ANTHROPIC_API_KEY = ''
    ANTHROPIC_MODEL = 'claude-haiku-4-5-20251001'
    ANTHROPIC_MAX_TOKENS = '4096'
}
if (Test-Path -LiteralPath $taskDestination) {
    $taskExisting = Read-EnvValues ([System.IO.File]::ReadAllText($taskDestination))
    foreach ($taskName in @('ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL', 'ANTHROPIC_MAX_TOKENS')) {
        if ($taskExisting.ContainsKey($taskName) -and $taskExisting[$taskName]) { $taskNewValues[$taskName] = $taskExisting[$taskName] }
    }
    $taskBackupDirectory = Join-Path $taskRoot '.tools'
    New-Item -ItemType Directory -Force -Path $taskBackupDirectory | Out-Null
    $taskBackup = Join-Path $taskBackupDirectory ("env-before-anthropic-" + [guid]::NewGuid().ToString('N') + '.bak')
    Copy-Item -LiteralPath $taskDestination -Destination $taskBackup
    Write-Output "Copia privada del entorno anterior: $taskBackup"
}
foreach ($taskName in $taskNewValues.Keys) { $taskText = Set-EnvValue $taskText $taskName $taskNewValues[$taskName] }
[System.IO.File]::WriteAllText($taskDestination, $taskText, [System.Text.UTF8Encoding]::new($false))
Write-Output "Entorno preparado: proyecto $Project, volumen $($taskMounts[0].Name), base $($taskValues['MONGODB_DB'])."
Write-Output 'No se han arrancado contenedores ni modificado datos. Schema2/schema3 se conservan. Añade la clave de Anthropic en el destino; reposición automática desactivada.'
