# Copia, restauración y ensayos aislados de TrAIner

**Procedimiento vigente para el entorno existente (2026-11-18):** usar el apartado
«OpenRouter y .env normal» al final. Las instrucciones anteriores con .env.schema3
corresponden a la adopción original de fase 3. No copiar .env.example sobre un
entorno con datos: esa plantilla describe una instalación nueva.

Nota posterior a fase 3 (2026-11-17): la reposición usa un proveedor inyectable.
El arranque habitual no tiene proveedor ni worker activado; no se habilita una
simulación mediante variables de entorno. El simulado solo existe en pruebas.
Los nuevos campos reposicion de Bancos forman parte de futuras copias; un lease
pendiente se recupera al caducar únicamente si se inyecta un proveedor. Sin él,
Vue muestra falta de configuración, aunque la copia contenga una tarea antigua.
No se ha restaurado ni modificado el volumen de desarrollo durante esta tarea.

## Ensayo reproducible de copia y restauración (actualizado para fase 3)

Desde la raíz del repositorio, con Docker Desktop activo, ejecutar en PowerShell:

```powershell
./scripts/Test-BackupRestore.ps1
```

En este equipo, si la construcción requiere la CA local ya configurada:

```powershell
./scripts/Test-BackupRestore.ps1 -BuildOverride .tools/compose.test.ca.yml
```

El script utiliza compose.test.yml y un nombre de proyecto nuevo. MongoDB tiene
red propia, almacenamiento tmpfs y ningún puerto publicado. No usa el servicio
mongodb ni el volumen mongodb_data de desarrollo. Origen y destino tienen nombres
trainer_test_backup_<uuid>_source/restored. El comprobador rechaza otros nombres.
Desde fase 3 la instancia es un replica set autenticado de un nodo. El verificador
añade diez preguntas sintéticas a la base de ensayo para disponer de dos bloques;
no cambia el seed de la aplicación ni la base de desarrollo. La copia incluye Progreso.

El recorrido ejecutado es:

1. Construir la imagen de pruebas y arrancar únicamente MongoDB del proyecto temporal.
2. Arrancar FastAPI mediante TestClient contra una base vacía, inicializar índices
   y ejecutar el seed. Crear un usuario por POST /auth/register y autenticarlo.
3. Crear y completar un intento. Crear otro y guardar una respuesta de borrador.
4. Cerrar la aplicación de origen para dejar de escribir y capturar sus documentos
   e índices. El destino debe estar vacío antes de restaurar.
5. Ejecutar mongodump --archive con compresión y copiar el archivo a .tools.
6. Ejecutar mongorestore con nsFrom/nsTo para restaurar en el nombre nuevo.
7. Comparar todos los documentos e índices ANTES del lifespan de FastAPI. Así se
   evita atribuir a la restauración un índice reparado después por la aplicación.
8. Abrir FastAPI contra el destino, realizar login, consultar /users/me y ambos
   intentos con sus mismos IDs. Comprobar resultado y borrador y entregar el pendiente.
9. Verificar que el origen sigue idéntico y retirar los contenedores/red temporales.

La implementación de la verificación está en backend/scripts/backup_restore_check.py.
Las credenciales JWT y del alumno de ensayo son aleatorias. El estado de verificación
y el archivo contienen datos privados de prueba y permanecen en .tools/backup-<uuid>,
excluido de Git. El script muestra el SHA-256 del archivo y conserva los artefactos.
Restringir su acceso y eliminarlos manualmente cuando termine su utilidad. El
chmod aplicado dentro del contenedor no sustituye las ACL del host Windows.

## Comandos de copia y restauración

El script contiene los comandos efectivos. De forma equivalente, una vez preparado
el proyecto aislado y sus nombres, sus operaciones centrales son:

```powershell
# Variables obtenidas de la ejecución del script, nunca de la base de desarrollo.
$taskProject = Read-Host 'Proyecto aislado'
$taskSource = Read-Host 'Base de origen del ensayo'
$taskTarget = Read-Host 'Base de destino vacía del ensayo'
$taskBackup = Join-Path (Get-Location) '.tools/copia-ensayo.archive.gz'

$taskDump = 'mongodump --host localhost --username "$MONGO_APP_USER" --password "$MONGO_APP_PASSWORD" --authenticationDatabase admin --db "$1" --archive=/tmp/backup.archive.gz --gzip'
docker compose -p $taskProject -f compose.test.yml exec -T mongodb-test sh -c $taskDump sh $taskSource
if ($LASTEXITCODE -ne 0) { throw 'Falló la copia' }
docker compose -p $taskProject -f compose.test.yml cp mongodb-test:/tmp/backup.archive.gz $taskBackup
if ($LASTEXITCODE -ne 0) { throw 'Falló la extracción' }
Get-FileHash -Algorithm SHA256 -LiteralPath $taskBackup

# Para restaurar posteriormente desde el archivo conservado en el host:
docker compose -p $taskProject -f compose.test.yml cp $taskBackup mongodb-test:/tmp/backup.archive.gz
if ($LASTEXITCODE -ne 0) { throw 'Falló la carga del archivo' }
$taskRestore = 'mongorestore --host localhost --username "$MONGO_APP_USER" --password "$MONGO_APP_PASSWORD" --authenticationDatabase admin --archive=/tmp/backup.archive.gz --gzip --nsInclude "$1.*" --nsFrom "$1.*" --nsTo "$2.*" --stopOnError'
docker compose -p $taskProject -f compose.test.yml exec -T mongodb-test sh -c $taskRestore sh $taskSource $taskTarget
if ($LASTEXITCODE -ne 0) { throw 'Falló la restauración' }
```

Los valores de autenticación del ejemplo los resuelve el contenedor de pruebas.
Su usuario permite operar en las bases efímeras. No trasladar esos permisos ni
esas credenciales al despliegue real. El script automatizado comprueba el ámbito
y la aplicación; los comandos manuales exigen verificar proyecto y destino.
No se utiliza --drop ni --noIndexRestore. Un destino ya utilizado requiere otro
nombre vacío para evitar mezclar datos o índices anteriores.

## Copiar una base que deba conservarse

Para una copia futura de datos reales, detener antes todos los escritores y el
seed. No arrancar código nuevo para inventariar un esquema antiguo: el lifespan
puede crear índices y sanear pendientes. Conservar también la revisión de código,
las opciones de colecciones, versiones de MongoDB/Database Tools y un inventario
de índices y recuentos. Usar herramientas compatibles con la versión del servidor.

Este es el comando interactivo de copia en el servicio MongoDB de desarrollo.
No se ha ejecutado durante la fase 2B:

```powershell
$taskUser = Read-Host 'Usuario autorizado para la copia'
$taskDatabase = Read-Host 'Base a conservar'
docker compose exec mongodb mongodump --host localhost --username $taskUser --authenticationDatabase admin --db $taskDatabase --archive=/tmp/trainer-backup.archive.gz --gzip
if ($LASTEXITCODE -ne 0) { throw 'Falló la copia' }
# La herramienta solicita la contraseña interactivamente.
docker compose cp mongodb:/tmp/trainer-backup.archive.gz .tools/trainer-backup.archive.gz
if ($LASTEXITCODE -ne 0) { throw 'Falló la extracción' }
Get-FileHash -Algorithm SHA256 -LiteralPath .tools/trainer-backup.archive.gz
```

Crear el directorio privado .tools previamente si no existe. El authenticationDatabase
del ejemplo corresponde a una cuenta administrativa; una cuenta de aplicación se
autentica en su propia base y debe tener permisos adecuados para las operaciones.
Evitar contraseñas reales dentro de comandos guardados o del historial de consola.

El dump lógico de una base preserva documentos, metadatos e índices. No copia el
volumen completo ni provisiona las cuentas de autenticación de otra instancia.
El destino debe tener usuarios MongoDB configurados de forma segura. La colección
Usuarios de TrAIner, incluidos sus hashes, sí forma parte de la copia de aplicación.
Con standalone y escritores activos no se obtiene aquí una instantánea consistente
entre todas las colecciones; por eso el procedimiento exige una pausa de escrituras.

## Ensayar una migración futura sobre una copia

1. Mantener el archivo original privado, registrar su hash y restaurarlo en una
   instancia y base nuevas. Nunca reutilizar MONGODB_URI/MONGODB_DB de desarrollo
   para el destino del ensayo. Configurar explícitamente el URI y la base del clon.
2. Comprobar documentos e índices antes de arrancar aplicaciones o migradores.
   Capturar IDs, recuentos y checksums y conservar la revisión anterior de código.
3. Ejecutar el migrador aprobado solo sobre el clon. Registrar versión de esquema,
   cambios esperados y checkpoints; repetir e interrumpirlo para comprobar recuperación.
4. Comparar todos los datos que deban conservarse y los índices del esquema esperado.
   Arrancar la aplicación compatible con el destino y repetir login, perfil,
   recuperación, borrador, entrega y aislamiento entre usuarios.
5. Volver a restaurar el archivo original en otro clon para ensayar rollback con
   su código compatible. Nunca ejecutar código viejo sobre un esquema nuevo.
6. Autorizar por separado cualquier corte real. Si hubo escrituras desde la copia,
   deben reconciliarse antes de restaurar para evitar perder esa actividad.

Esta fase no incluye un migrador del banco agrupado anterior. Ese formato se
rechaza antes de crear colecciones o índices. Los snapshots anteriores siguen
siendo legibles cuando se encuentran en una base compatible; no necesitan el
banco original para corregirse. Para adoptar el esquema actual desde datos de
prueba descartables, preparar una instalación nueva y ejecutar app.seed.

## Instalación vacía con el esquema actual (operaciones manuales pendientes)

La base actual contiene datos de prueba y no se ha migrado ni modificado. Para
adoptar este cambio conservando el volumen anterior, crear otro proyecto Compose
y otro archivo de entorno. Estos comandos son instrucciones para el desarrollador;
no se han ejecutado contra su entorno durante el ensayo.

1. Detener los servicios anteriores desde su proyecto habitual para liberar los
   puertos 8000, 5173 y, si procede, 8081. `docker compose stop` no elimina su volumen.
   No usar `down -v`, `volume rm` ni `prune`.
2. Crear un archivo local nuevo y sustituir todos sus marcadores por secretos
   aleatorios. Elegir `MONGODB_DB=trainer_schema3` y mantener coherente la URI
   para ejecuciones fuera de Compose. El Compose construye su propia URI con las
   variables de usuario, contraseña y base. No basta con cambiar MONGODB_DB en
   el volumen anterior: el script mongo-init solo provisiona usuarios al inicializar
   un directorio de datos vacío.

```powershell
# Solo si todavía no existe: no sobrescribir una configuración local preparada.
if (Test-Path -LiteralPath .env.schema3) { throw '.env.schema3 ya existe; revisarlo manualmente' }
Copy-Item -LiteralPath .env.example -Destination .env.schema3
# Editar .env.schema3 y sustituir los marcadores antes de continuar.
```

3. Comprobar que no existe previamente el proyecto/volumen trainer-schema3.
   El nombre de proyecto crea un volumen propio trainer-schema3_mongodb_data.
   Desde fase 3 el overlay replica es necesario para crear nuevas reservas.
   Arrancar y sembrar explícitamente:

```powershell
docker compose --env-file .env.schema3 -p trainer-schema3 -f docker-compose.yml -f compose.replica.yml up --build -d
if ($LASTEXITCODE -ne 0) { throw 'Falló el arranque; revisar antes de continuar' }
docker compose --env-file .env.schema3 -p trainer-schema3 -f docker-compose.yml -f compose.replica.yml exec backend python -m app.seed
if ($LASTEXITCODE -ne 0) { throw 'Falló el seed' }
docker compose --env-file .env.schema3 -p trainer-schema3 -f docker-compose.yml -f compose.replica.yml ps
Invoke-RestMethod http://localhost:8000/health
```

4. Abrir http://localhost:5173. Registrar un usuario nuevo, iniciar sesión,
   practicar Present Simple, guardar un borrador al abandonar un campo, continuar
   el mismo pendiente y entregarlo. En una base vacía, el seed carga un banco A1,
   diez preguntas manuales y generacion_banco v2 activa. Las pruebas automatizadas
   verifican esta persistencia; el arranque por sí solo crea colecciones e índices,
   pero no carga el contenido.
   Tras consumir ese único bloque inicial, la práctica mostrará el banco agotado.
   No hay reposición automática ni ampliación del seed a veinte preguntas reales.
5. Utilizar en adelante `--env-file .env.schema3 -p trainer-schema3 -f docker-compose.yml -f compose.replica.yml` en todos los
   comandos de ese entorno, incluido el perfil opcional `--profile tools`. Conservar
   el volumen anterior hasta revisar el resultado. Para volver al código anterior,
   detener el proyecto nuevo y usar una revisión compatible con la base anterior.

.env.schema3 y sus secretos están excluidos por la regla .env.*. No mostrar
`docker compose config` en informes compartidos: puede interpolar credenciales.
Esta adopción manual todavía no está validada contra la instalación de desarrollo.

## Conservar usuarios e intentos de una base 2B

No aplicar el overlay directamente sobre el volumen original durante el ensayo.
Detener los escritores 2B, hacer la copia de su base según el procedimiento anterior
y registrar el hash. Preparar el entorno privado .env.schema3 y proyecto nuevo del
apartado anterior, pero arrancar primero SOLO MongoDB:

```powershell
docker compose --env-file .env.schema3 -p trainer-schema3 -f docker-compose.yml -f compose.replica.yml up -d --wait mongodb
if ($LASTEXITCODE -ne 0) { throw 'No está disponible el primario de la copia' }
# .tools/trainer-backup.archive.gz debe ser la copia privada de la base 2B.
docker compose --env-file .env.schema3 -p trainer-schema3 -f docker-compose.yml -f compose.replica.yml cp .tools/trainer-backup.archive.gz mongodb:/tmp/trainer-backup.archive.gz
if ($LASTEXITCODE -ne 0) { throw 'Falló la carga de la copia' }
$taskSource = Read-Host 'Nombre de la base 2B incluida en el archivo'
$taskRestore = 'mongorestore --host localhost --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin --archive=/tmp/trainer-backup.archive.gz --gzip --nsInclude "$1.*" --nsFrom "$1.*" --nsTo "$MONGODB_DB.*" --stopOnError'
docker compose --env-file .env.schema3 -p trainer-schema3 -f docker-compose.yml -f compose.replica.yml exec -T mongodb sh -c $taskRestore sh $taskSource
if ($LASTEXITCODE -ne 0) { throw 'Falló la restauración; no arrancar el backend' }
```

El destino de aplicación debe estar vacío; los usuarios de autenticación MongoDB
ya provisionados no son la colección Usuarios de la aplicación. Comparar los
documentos e índices con el inventario de la copia ANTES del arranque. Revisar
especialmente los snapshots/orígenes; no ejecutar todavía el seed para inventariar.
Después arrancar backend y frontend del mismo proyecto con up --build -d.

El arranque crea Progreso e inicializa las claves ausentes desde la mayor secuencia
de cada alumno/banco en los intentos 2B conservados, antes de aceptar peticiones.
No modifica los snapshots ni suma sus preguntas: diez intentos que repiten 1–10
inicializan 10. Retomar y entregar no avanzan el cursor. Descartar consume el bloque.
Sin intentos conservados se comienza en cero; los descartes físicos de 2B no se
pueden inferir. Los orígenes legacy sin secuencia no permiten reconstrucción.
Revisar manualmente la frontera si el contenido 2B se editó con huecos de secuencia.

Comprobar login, borrador, resultados e IDs anteriores, cursor y siguiente bloque.
Con el seed original de diez preguntas, un alumno que ya reservó 1–10 encontrará
el banco agotado; es el resultado esperado. Mantener la copia y el volumen anterior
hasta aceptar el ensayo. No revertir ejecutando código anterior sobre el esquema
nuevo: volver a una copia y una revisión compatibles. No se ha realizado esta
adopción sobre el volumen de desarrollo.

## Evidencia de fase 3 del 2026-11-17

Se ha repetido el script en un proyecto temporal distinto con MongoDB 8.0.15,
replica set autenticado y PyMongo 4.18.2. Resultado: 29 documentos restaurados,
cero fallos, siete colecciones, veinte preguntas y un Progreso con cursor 20.
Se conservan usuario, completado y pendiente con borrador. Se compararon todos
los documentos e índices antes del lifespan y se verificó el flujo de aplicación
en el destino, además de que el origen permanecía idéntico. El índice
usuario_banco_unique también se restauró. Artefactos privados en
.tools/backup-cc5a0b02499d4f15b234a68b4210974f; contenedores y red retirados.
La evidencia siguiente pertenece al ensayo histórico de 2B, sin Progreso.

## Evidencia del ensayo del 2026-11-16

El script terminó correctamente con MongoDB 8.0.15. Se copiaron y restauraron
18 documentos de seis colecciones: un usuario, dos intentos, un catálogo,
un banco, diez preguntas y tres prompts. Se verificó igualdad íntegra de
documentos e índices antes del arranque, incluido el índice único parcial de
pendientes. Los endpoints de FastAPI validaron login/perfil, resultado completado,
borrador recuperado y entrega posterior en el destino. El origen permaneció idéntico.
La comprobación usa la aplicación ASGI real con TestClient y MongoDB real aislado.

Referencias oficiales: [mongodump](https://www.mongodb.com/docs/database-tools/mongodump/)
y [mongorestore](https://www.mongodb.com/docs/database-tools/mongorestore/).

## OpenRouter y .env normal (2026-11-18)

### Configuración observada y conservación

Se inspeccionaron los metadatos Docker, no los documentos de desarrollo:
proyecto trainer-schema3, volumen trainer-schema3_mongodb_data montado en
/data/db, base trainer, réplica configurada trainer-local y host mongodb:27017.
La configuración del contenedor usa docker-compose.yml y compose.replica.yml.
Las credenciales de .env.schema3 coinciden con los contenedores, sin mostrarlas.
No se ha ejecutado la adopción ni arrancado esos servicios durante este trabajo.

El Compose habitual ya incorpora el replica set y toma .env. La identidad del
proyecto y el nombre explícito de volumen se guardan en ese archivo. El volumen
existente se declara externo: si no existe, Docker falla en lugar de crear uno
vacío. .env.example permite una instalación nueva con volumen propio; no es la
fuente para sustituir la configuración de fase 3.

Use-Phase3Environment.ps1 inspecciona los contenedores existentes, comprueba
base/credenciales/réplica, conserva el JWT y prepara .env desde .env.schema3.
Antes guarda una copia privada del .env anterior en .tools. Conserva una clave
OpenRouter que ya estuviera en el destino y fija BANK_REFILL_ENABLED=false.
No ejecuta up, seed, migraciones ni operaciones MongoDB. No modifica ni elimina
.env.schema2/.env.schema3. Los respaldos de entorno contienen secretos: no compartir.

### Configuración de OpenRouter

Usar una clave OpenRouter del mismo workspace que contiene la clave Anthropic.
En su configuración BYOK, para Anthropic seleccionar Shared capacity fallback:
**Never use shared capacity for any model on this provider**. La restricción
provider.only no controla por sí sola ese fallback de capacidad compartida.
La clave Anthropic permanece en OpenRouter; no se copia al backend ni a Vue.

El adaptador envía una sola solicitud Chat Completions, únicamente a Anthropic,
sin fallbacks, modelos alternativos ni redirects, con JSON Schema obligatorio.
Rechaza respuestas sin confirmación BYOK/Anthropic, con modelo distinto, incompletas
o contrarias al contrato. Comprobar BYOK después de responder no puede deshacer
el coste de una configuración incorrecta: configurar primero la restricción del
workspace. Véase [BYOK oficial](https://openrouter.ai/docs/guides/overview/auth/byok).

### Comandos manuales de PowerShell

Con Docker Desktop activo, desde la raíz del repositorio:

```powershell
./scripts/Use-Phase3Environment.ps1
notepad .env
```

Guardar en ese archivo OPENROUTER_API_KEY con la clave propia. Mantener
BANK_REFILL_ENABLED=false. El modelo inicial es anthropic/claude-haiku-4.5;
se puede cambiar OPENROUTER_MODEL por otro identificador Anthropic compatible
con structured outputs. OPENROUTER_MAX_COMPLETION_TOKENS limita la salida.
No cambiar los valores MongoDB/JWT copiados de fase 3.

El script debe haber dejado COMPOSE_PROJECT_NAME=trainer-schema3,
MONGODB_VOLUME_NAME=trainer-schema3_mongodb_data y MONGODB_VOLUME_EXTERNAL=true.
Después de guardar y cerrar el editor:

```powershell
docker compose config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Revisa .env antes de continuar' }

# En este equipo existe el override privado de CA para construir las imágenes.
docker compose -f docker-compose.yml -f .tools/compose.ca.yml build backend frontend
if ($LASTEXITCODE -ne 0) { throw 'Falló la construcción' }

# Pausar los escritores y arrancar únicamente MongoDB, con su mismo volumen.
docker compose --profile tools stop backend frontend mongo-express
docker compose up -d --wait mongodb
if ($LASTEXITCODE -ne 0) { throw 'MongoDB no está disponible; no continuar' }

# Solo lectura: no arranca FastAPI ni ejecuta su inicialización o el seed.
docker compose run --rm --no-deps -T backend python -m scripts.data_snapshot | Set-Content -Encoding utf8 .tools/phase3-before-env.json
if ($LASTEXITCODE -ne 0) { throw 'No se pudo inventariar fase 3' }
Get-Content .tools/phase3-before-env.json

docker compose --profile tools up -d --wait
if ($LASTEXITCODE -ne 0) { throw 'Falló el arranque con .env' }
Get-Content -Raw .tools/phase3-before-env.json | docker compose exec -T backend python -m scripts.data_snapshot --compare-stdin
if ($LASTEXITCODE -ne 0) { throw 'El inventario cambió: detenerse y revisar' }
Invoke-RestMethod http://localhost:8000/health
docker inspect --format '{{range .Mounts}}{{println .Name .Destination}}{{end}}' trainer-schema3-mongodb-1
```

Si no se necesita una CA local, omitir únicamente el -f .tools/compose.ca.yml del
build. En este workspace el archivo existe y ya se ha utilizado para builds.
No mostrar docker compose config sin --quiet: interpola secretos.

El inventario muestra base, replica set real, recuentos y hashes de documentos
e índices de todas las colecciones. No imprime usuarios, hashes de contraseña,
respuestas ni credenciales. La comparación debe indicar igualdad y el volumen
debe seguir siendo trainer-schema3_mongodb_data. Verificar además en
http://localhost:5173 el login de un usuario anterior y su intento/borrador.
No ejecutar el seed, down -v, volume rm, prune ni recrear una base vacía durante
esta adopción. Conservar schema2/schema3 y la copia del .env hasta aceptar el resultado.

### Ensayo real manual: una llamada, cinco preguntas, sin publicar

Ejecutar solo después de verificar la configuración BYOK y el arranque:

```powershell
docker compose exec -T backend python -m scripts.preview_openrouter --cantidad 5
```

Esta es la única llamada real del procedimiento. Imprime JSON con modelo
solicitado/utilizado, proveedor, confirmación BYOK, tokens, id de llamada si está
disponible, versión de prompt y las cinco preguntas. Lee banco/ficha/plantilla
existentes; no crea tareas, usuarios, intentos, progreso ni preguntas en MongoDB.
No hay opción de publicar ni reintento automático. Rechaza el ensayo si la
reposición está activada. Los errores omiten secretos y cuerpos HTTP.
Revisar también manualmente corrección, alternativas y distractores: pasar el
contrato no acredita calidad lingüística. La publicación queda para otra acción.

Tras el ensayo se puede repetir la comparación de inventario anterior para
confirmar que no se ha publicado nada. Mantener BANK_REFILL_ENABLED=false.
El arranque cotidiano pasa a ser docker compose --profile tools up -d --wait,
sin --env-file ni overlay de replica. El perfil tools es opcional.

### Evidencia local y pendientes

Se ejecutaron 252 pruebas backend y 73 frontend, builds y comprobaciones de
sintaxis/formato. OpenRouter se probó exclusivamente con MockTransport; una
prueba del ensayo valida una sola solicitud y compara inventarios MongoDB antes
y después sobre una base aislada. La adopción PowerShell se ensayó con Docker y
secretos ficticios, incluso rechazo sin volumen. El mapeo Compose se validó sin
arrancar servicios. La llamada real y la adopción del entorno de desarrollo no
se han realizado; deben verificarlas el desarrollador con los comandos anteriores.

Con clave configurada y reposición apagada, Vue indica reposicion_desactivada; no confunde ese modo con una clave ausente.

## Ensayo directo con Anthropic (sin OpenRouter)

Alternativa manual que conserva el adaptador OpenRouter y el overlay TLS local.
Editar el .env de la raíz; no copiar .env.example sobre el archivo existente.
Añadir estas variables (sustituir el marcador únicamente en el archivo local):

```dotenv
ANTHROPIC_API_KEY=TU_CLAVE_REAL_DE_ANTHROPIC
ANTHROPIC_MODEL=claude-haiku-4-5-20251001
ANTHROPIC_MAX_TOKENS=4096
BANK_REFILL_ENABLED=false
```

No cambiar COMPOSE_FILE, proyecto, volumen, URI ni credenciales existentes.
El modelo directo usa el identificador de Anthropic, sin prefijo anthropic/.
Mantener el identificador fechado: se comprueba que el modelo devuelto coincide.

Desde PowerShell en la raíz del proyecto:

```powershell
docker compose up -d --no-deps --build --wait backend
docker compose exec -T backend python -m scripts.preview_openrouter --proveedor anthropic --cantidad 5
```

El nombre del módulo se conserva para no romper el comando anterior; sin
--proveedor continúa seleccionando OpenRouter. El nuevo argumento selecciona
Anthropic exclusivamente para ese ensayo; no cambia la factoría del worker.
El primer comando recrea solo el backend e incorpora las variables de .env.
El segundo envía una sola petición a /v1/messages, sin reintentos ni redirecciones.
Lee el banco Present Simple A1, su ficha y el prompt activo desde MongoDB;
valida las cinco preguntas y muestra modelo, tokens, identificador y contenido.
No publica ni modifica bancos, progreso, intentos o prompts. El coste desconocido
no se estima; byok_confirmado=null corresponde a API directa, sin OpenRouter.
Revisar la salida en pantalla. No repetir el comando si solo se desea una llamada.

La previsualización distingue preguntas_recibidas,
preguntas_descartadas_duplicadas y preguntas_candidatas. El campo preguntas
contiene únicamente candidatas nuevas respecto al banco consultado: se compara
el enunciado sin diferencias de mayúsculas ni de espacios, independientemente
de soluciones o distractores. Un lote de cinco duplicadas devuelve 5/5/0 y una
lista vacía. No se solicita automáticamente otro lote para rellenarlo.

Comparte el filtro con la reposición, que lo ejecuta dentro de su transacción
de publicación. La previsualización solo refleja el banco en el instante de
consulta y no reserva ni garantiza candidatas frente a publicaciones posteriores.

El prompt v2 recibe la ficha educativa (con ejemplos, algunos comunes al seed),
pero no una consulta de ejemplos del banco ni una lista dinámica de exclusión.
Sus restricciones generales de variedad no sustituyen el filtro del backend.
En este ajuste se conserva íntegra la versión persistida y el seed.

Este procedimiento se ha probado con transporte simulado y MongoDB aislado.
La disponibilidad, permisos, saldo y compatibilidad del servicio real quedan
pendientes del ensayo del autor. No se ha realizado ninguna llamada real.

## Prompt v3 y ensayo de lote completo

Se conserva v2. El seed prepara v3 inactiva y no cambia la elección previa de
prompt activo; una base vacía sigue arrancando con v2 hasta aprobación manual.
La nueva activación crea únicamente la versión ausente desde el mismo mecanismo
de seed y cambia el estado activo en una transacción corta del replica set.
No modifica fichas, preguntas, intentos, usuarios ni progreso.

Mantener estas variables en el .env local (la clave Anthropic ya existente):

```dotenv
BANK_REFILL_ENABLED=false
BANK_GENERATION_MAX_ROUNDS=3
```

Desde PowerShell en la raíz:

```powershell
# Actualizar solo el backend; conserva MongoDB y su volumen.
docker compose up -d --no-deps --build --wait backend

# Preparar/activar v3 de forma atómica; no realiza ninguna llamada LLM.
docker compose exec -T backend python -m scripts.activate_generation_prompt --version 3

# Ensayo completo: hasta 3 llamadas en total, nunca publica preguntas.
docker compose exec -T backend python -m scripts.preview_openrouter --proveedor anthropic --cantidad 5 --modo lote
```

El comando de lote puede consumir varias llamadas facturables; se pide únicamente
lo que falte después de cada ronda. Una ejecución no garantiza éxito: puede devolver
lote_incompleto_sin_publicar al alcanzar el límite y muestra las candidatas logradas.
La preparación del prompt es la misma que utiliza la reposición. La v3 excluye
los enunciados actuales del banco y las candidatas ya aceptadas, y retira de los
materiales pedagógicos las líneas que contienen enunciados excluidos. Con el seed
A1 actual se incluyen sus diez enunciados y se retiran tres ejemplos coincidentes.
No se altera el fichero de la ficha ni su hash/versionado; la composición v3 usa
un extracto filtrado, con trazabilidad al original.

Para diagnóstico, una sola llamada (también es el modo predeterminado):

```powershell
docker compose exec -T backend python -m scripts.preview_openrouter --proveedor anthropic --cantidad 5 --modo diagnostico
```

Campos de revisión: candidatas_primera_ronda, duplicadas_primera_ronda,
preguntas_recibidas (suma de rondas), preguntas_descartadas_duplicadas,
preguntas_candidatas (total final), numero_llamadas, lote_completo y rondas.
Los tokens de entrada y salida se acumulan; null indica que falta información,
no consumo cero. rondas incluye ids disponibles y cantidades solicitadas/recibidas.
Ningún modo de preview escribe ni publica. No ejecutar ambos modos si solo se
quiere un ensayo. Para volver a v2 sin borrar texto/versiones, usar el comando
activate_generation_prompt con --version 2, manteniendo reposición apagada.

La reposición automática solo publica una cantidad exacta, validada y nueva tras
recomprobar duplicados dentro de su transacción. Un lote incompleto no publica
parcialmente; registra candidatos logrados y error. rondas_consumidas en la tarea
limita también los reintentos del worker. Las candidatas se retienen en memoria
durante la ejecución del lote; no se añade recuperación de candidatas tras una
caída del proceso. Una llamada fallida consume su plaza de ronda preventivamente.
El timeout y lease existentes se conservan: el timeout limita el conjunto de rondas
de un intento del worker, no se multiplica por el número de llamadas.

El ensayo real y la activación en desarrollo corresponden al autor. Las pruebas
locales usan proveedores simulados y una base aislada, sin llamadas reales.
## Diagnóstico de validación Anthropic y captura local de texto

La validación se conserva. Al fallar Anthropic, la CLI emite JSON con estado
fallo_generacion_sin_publicar y termina con código 1: no es publicación ni éxito.
El diagnóstico incluye ronda, modelo solicitado/utilizado, id_llamada,
stop_reason, tokens de entrada/salida y preguntas_recibidas si el texto es JSON
legible con un array preguntas. Un valor null significa desconocido/no disponible.
Los tokens del diagnóstico de error corresponden a la llamada fallida, no al
acumulado del lote. En éxito se mantiene el informe acumulado existente.

Errores expone campo, regla y detalle sin valores de entrada de Pydantic. Ejemplos:
preguntas.17.respuesta_principal / string_too_long (índices desde cero), una regla
coherente de pregunta en preguntas.17, cantidad_exacta en preguntas o json_valido
con línea/columna en content.0.text. stop_reason=max_tokens se diferencia del
incumplimiento de un campo; no se aumenta el límite de tokens automáticamente.
El diagnóstico no permite determinar retroactivamente la causa de la llamada
anterior cuyo detalle no se conservó.

--guardar-texto es opcional y exclusivo del ensayo Anthropic. Guarda únicamente
los bloques textuales del modelo (concatenados si hay varios), tanto en éxito
como antes de rechazar su contenido. Nunca guarda el sobre HTTP, petición,
cabeceras ni la clave; redacta la clave Anthropic configurada si apareciera en el
texto. Un error de escritura se informa sin sustituir el de validación. Si no
se puede aislar texto (por ejemplo, fallo HTTP o sobre inválido), no se inventa
un archivo. La protección de tamaño existente de 1 MiB se mantiene.

Para repetir una única llamada de 30 y conservar el texto en Windows, mantener
BANK_REFILL_ENABLED=false en .env. Desde PowerShell, en la raíz:

```powershell
docker compose build backend
New-Item -ItemType Directory -Force .tools\diagnosticos | Out-Null
$diagnosticosTrainer = (Resolve-Path .tools\diagnosticos).Path
docker compose run --rm --no-deps --volume "${diagnosticosTrainer}:/app/.tools" backend python -m scripts.preview_openrouter --proveedor anthropic --cantidad 30 --modo diagnostico --guardar-texto
```

El contenedor temporal usa la imagen actual, .env y el overlay de CA ya configurado;
no inicia un worker ni recrea MongoDB. El bind mount conecta /app/.tools únicamente
con .tools/diagnosticos del host, sin montar el resto de archivos locales de .tools.
Los nombres son anthropic-ronda-N-UUID.txt; la salida muestra la ruta del contenedor.
No hay que copiar archivos desde el contenedor después: ya están en Windows.
La carpeta está ignorada por Git. Los archivos guardados por el ensayo se conservan
para inspección local; sin --guardar-texto no se escriben archivos del modelo.

Para ensayar el flujo de hasta tres rondas, cambiar --modo diagnostico por --modo
lote; puede facturar varias llamadas. No ejecutar ambos comandos si solo se quiere
una llamada. Este ajuste no cambia el prompt activo ni publica candidatas.