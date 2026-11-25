# Prueba de adopción con Docker y credenciales ficticios; nunca llama al daemon.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskRelative = '.tools/env-adoption-test-' + [guid]::NewGuid().ToString('N')
$taskDirectory = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $taskRelative))
$taskTools = [System.IO.Path]::GetFullPath((Join-Path $taskRoot '.tools'))
if (-not $taskDirectory.StartsWith($taskTools + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) { throw 'Ruta de pruebas fuera de .tools' }
New-Item -ItemType Directory -Path $taskDirectory | Out-Null
$global:trainerMockMissingVolume = $false
function docker {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq 'ps') {
        if ($args -contains 'label=com.docker.compose.service=mongodb') { return 'mock-mongo' }
        return 'mock-backend'
    }
    if ($args[0] -ne 'inspect') { throw 'El ensayo no permite operaciones Docker de escritura' }
    if ($args[1] -eq 'mock-mongo') {
        $taskMounts = @(@{ Type='volume'; Name='trainer-mock_mongodb_data'; Destination='/data/db' })
        if ($global:trainerMockMissingVolume) { $taskMounts = @() }
        return ConvertTo-Json -Depth 5 -InputObject @(@{
            Mounts=$taskMounts
            Config=@{ Env=@(
                'MONGO_INITDB_ROOT_USERNAME=admin', 'MONGO_INITDB_ROOT_PASSWORD=fake-root-password',
                'MONGO_APP_USER=app', 'MONGO_APP_PASSWORD=fake-app-password', 'MONGODB_DB=trainer',
                'MONGO_REPLICA_SET=trainer-local', 'MONGO_REPLICA_HOST=mongodb:27017'
            ) }
        })
    }
    return ConvertTo-Json -Depth 5 -InputObject @(@{ Config=@{ Env=@('MONGODB_DB=trainer', 'JWT_SECRET=fake-jwt-012345678901234567890123456789') } })
}
try {
    $taskSource = Join-Path $taskDirectory 'source.env'
    $taskDestination = Join-Path $taskDirectory 'destination.env'
    @'
MONGO_ROOT_USER=admin
MONGO_ROOT_PASSWORD="fake-root-password"
MONGO_APP_USER=app
MONGO_APP_PASSWORD=fake-app-password
MONGODB_DB=trainer
JWT_SECRET=fake-jwt-012345678901234567890123456789
'@ | Set-Content -LiteralPath $taskSource -Encoding utf8
    $taskOverlay = Join-Path $taskDirectory 'compose.ca.yml'
    'services: {backend: {environment: {SSL_CERT_FILE: /run/fake-ca.pem}}}' | Set-Content -LiteralPath $taskOverlay -Encoding utf8
    @("ANTHROPIC_API_KEY=sk-ant-existing-test-key-not-real", 'COMPOSE_PATH_SEPARATOR=;', "COMPOSE_FILE=docker-compose.yml;$taskRelative/compose.ca.yml") | Set-Content -LiteralPath $taskDestination -Encoding utf8
    $taskBefore = (Get-FileHash -LiteralPath $taskSource).Hash
    & (Join-Path $PSScriptRoot 'Use-Phase3Environment.ps1') -Project trainer-mock -SourceEnv "$taskRelative/source.env" -DestinationEnv "$taskRelative/destination.env" | Out-Null
    $taskResult = Get-Content -LiteralPath $taskDestination -Raw
    foreach ($taskRequired in @(
        'COMPOSE_PROJECT_NAME=trainer-mock', 'MONGODB_VOLUME_NAME=trainer-mock_mongodb_data',
        'MONGODB_VOLUME_EXTERNAL=true', 'BANK_REFILL_ENABLED=false',
        'ANTHROPIC_API_KEY=sk-ant-existing-test-key-not-real', 'MONGODB_DB=trainer',
        'COMPOSE_PATH_SEPARATOR=;', "COMPOSE_FILE=docker-compose.yml;$taskRelative/compose.ca.yml"
    )) {
        if (-not $taskResult.Contains($taskRequired)) { throw 'El entorno generado no conserva la configuración esperada' }
    }
    if ((Get-FileHash -LiteralPath $taskSource).Hash -ne $taskBefore) { throw 'Se modificó el origen' }
    # Sin destino previo, conservar también el stack compatible del origen.
    $taskSourceWithCA = Join-Path $taskDirectory 'source-ca.env'
    $taskNewDestination = Join-Path $taskDirectory 'new.env'
    ((Get-Content -LiteralPath $taskSource -Raw) + "`nCOMPOSE_PATH_SEPARATOR=;`nCOMPOSE_FILE=docker-compose.yml;$taskRelative/compose.ca.yml`n") | Set-Content -LiteralPath $taskSourceWithCA -Encoding utf8
    & (Join-Path $PSScriptRoot 'Use-Phase3Environment.ps1') -Project trainer-mock -SourceEnv "$taskRelative/source-ca.env" -DestinationEnv "$taskRelative/new.env" | Out-Null
    if (-not (Get-Content -LiteralPath $taskNewDestination -Raw).Contains("COMPOSE_FILE=docker-compose.yml;$taskRelative/compose.ca.yml")) { throw 'Se perdió el overlay CA del origen' }
    foreach ($taskInvalidCompose in @("docker-compose.yml;$taskRelative/missing.yml", 'compose.test.yml')) {
        $taskInvalidText = $taskResult.Replace("docker-compose.yml;$taskRelative/compose.ca.yml", $taskInvalidCompose)
        Set-Content -LiteralPath $taskDestination -Value $taskInvalidText -Encoding utf8
        $taskInvalidHash = (Get-FileHash -LiteralPath $taskDestination).Hash
        $taskBlocked = $false
        try {
            & (Join-Path $PSScriptRoot 'Use-Phase3Environment.ps1') -Project trainer-mock -SourceEnv "$taskRelative/source.env" -DestinationEnv "$taskRelative/destination.env" | Out-Null
        } catch { $taskBlocked = $true }
        if (-not $taskBlocked -or (Get-FileHash -LiteralPath $taskDestination).Hash -ne $taskInvalidHash) { throw 'No se bloqueó un stack inexistente/incompatible antes de escribir' }
    }
    Set-Content -LiteralPath $taskDestination -Value $taskResult -Encoding utf8
    $taskDestinationHash = (Get-FileHash -LiteralPath $taskDestination).Hash
    $global:trainerMockMissingVolume = $true
    $taskRejected = $false
    try {
        & (Join-Path $PSScriptRoot 'Use-Phase3Environment.ps1') -Project trainer-mock -SourceEnv "$taskRelative/source.env" -DestinationEnv "$taskRelative/destination.env" | Out-Null
    } catch { $taskRejected = $true }
    if (-not $taskRejected -or (Get-FileHash -LiteralPath $taskDestination).Hash -ne $taskDestinationHash) { throw 'No se bloqueó la adopción sin volumen' }
    Write-Output 'Adopción simulada correcta: CA de origen/destino, proyecto/volumen y secretos conservados; bloqueo sin volumen o stack compatible.'
} finally {
    # Objetivo absoluto verificado arriba, exclusivo de este ensayo.
    Remove-Item -LiteralPath $taskDirectory -Recurse -Force
    Remove-Variable trainerMockMissingVolume -Scope Global
}
