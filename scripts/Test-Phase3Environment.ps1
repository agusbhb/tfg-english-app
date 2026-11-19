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
    'OPENROUTER_API_KEY=sk-or-v1-existing-test-key-not-real' | Set-Content -LiteralPath $taskDestination -Encoding utf8
    $taskBefore = (Get-FileHash -LiteralPath $taskSource).Hash
    & (Join-Path $PSScriptRoot 'Use-Phase3Environment.ps1') -Project trainer-mock -SourceEnv "$taskRelative/source.env" -DestinationEnv "$taskRelative/destination.env" | Out-Null
    $taskResult = Get-Content -LiteralPath $taskDestination -Raw
    foreach ($taskRequired in @(
        'COMPOSE_PROJECT_NAME=trainer-mock', 'MONGODB_VOLUME_NAME=trainer-mock_mongodb_data',
        'MONGODB_VOLUME_EXTERNAL=true', 'BANK_REFILL_ENABLED=false',
        'OPENROUTER_API_KEY=sk-or-v1-existing-test-key-not-real', 'MONGODB_DB=trainer'
    )) {
        if (-not $taskResult.Contains($taskRequired)) { throw 'El entorno generado no conserva la configuración esperada' }
    }
    if ((Get-FileHash -LiteralPath $taskSource).Hash -ne $taskBefore) { throw 'Se modificó el origen' }
    $taskDestinationHash = (Get-FileHash -LiteralPath $taskDestination).Hash
    $global:trainerMockMissingVolume = $true
    $taskRejected = $false
    try {
        & (Join-Path $PSScriptRoot 'Use-Phase3Environment.ps1') -Project trainer-mock -SourceEnv "$taskRelative/source.env" -DestinationEnv "$taskRelative/destination.env" | Out-Null
    } catch { $taskRejected = $true }
    if (-not $taskRejected -or (Get-FileHash -LiteralPath $taskDestination).Hash -ne $taskDestinationHash) { throw 'No se bloqueó la adopción sin volumen' }
    Write-Output 'Adopción simulada correcta: mismo proyecto/volumen, secretos conservados, origen intacto y rechazo sin volumen.'
} finally {
    # Objetivo absoluto verificado arriba, exclusivo de este ensayo.
    Remove-Item -LiteralPath $taskDirectory -Recurse -Force
    Remove-Variable trainerMockMissingVolume -Scope Global
}
