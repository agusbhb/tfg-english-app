# Ejecutar desde la raíz del repositorio. No modifica un .env existente.
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnv = Join-Path $taskRoot '.env'
if (Test-Path -LiteralPath $taskEnv) {
    throw 'Ya existe .env. Revísalo manualmente; no se ha sobrescrito.'
}
function New-TaskSecret {
    $taskBytes = New-Object byte[] 32
    $taskGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $taskGenerator.GetBytes($taskBytes) }
    finally { $taskGenerator.Dispose() }
    return (($taskBytes | ForEach-Object { $_.ToString('x2') }) -join '')
}
$taskContent = Get-Content -Raw -LiteralPath (Join-Path $taskRoot '.env.example')
foreach ($taskMarker in @(
    'CHANGE_ME_ROOT_PASSWORD', 'CHANGE_ME_APP_PASSWORD',
    'CHANGE_ME_GENERATE_A_RANDOM_SECRET', 'CHANGE_ME_EXPRESS_PASSWORD'
)) {
    $taskContent = $taskContent.Replace($taskMarker, (New-TaskSecret))
}
[System.IO.File]::WriteAllText($taskEnv, $taskContent, [System.Text.UTF8Encoding]::new($false))
Write-Output 'Configuración local creada en .env con secretos aleatorios (no mostrados).'
