# Copia, restauración y ensayos aislados de TrAIner

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
