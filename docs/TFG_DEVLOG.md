# Registro técnico de desarrollo de TrAIner

Este archivo conserva decisiones, justificaciones y resultados útiles para la
memoria del TFG. Las reglas proporcionadas están actualmente en `AGENTS.md`,
en la raíz del repositorio.
El README actual contiene únicamente el título del proyecto; los comandos de
esta revisión se recogen más abajo. Las decisiones arquitectónicas que
necesiten un análisis independiente podrán documentarse en `docs/decisions/`.

## 2026-10-07 — Primera entrega: base de datos y autenticación

Registro retrospectivo de la entrega realizada en esta misma fecha, a partir
de la implementación, las comprobaciones ejecutadas y su documentación.

### Objetivo

Preparar el entorno local, cargar el catálogo académico inicial y permitir
registrar un alumno, iniciar sesión y consultar su perfil. El alcance procede
de `TrAIner_01_BD_autenticacion.md`, proporcionado por el autor del TFG.

### Implementación

- Backend FastAPI con conexión asíncrona a MongoDB durante el lifespan.
- Docker Compose con MongoDB autenticado, backend, volumen persistente y
  mongo-express opcional mediante el perfil `tools`.
- Cinco colecciones: `Usuarios`, `Asignaturas`, `Prompts`, `BancoEjercicios` y
  `Tests`, con modelos Pydantic e índices inicializados de forma repetible.
- Seed explícito de Inglés → 1.º ESO → Present Simple, con diez ejemplos
  manuales de frases con huecos y dos plantillas borrador inactivas.
- Rutas `GET /health`, `GET /asignaturas`, `POST /auth/register`,
  `POST /auth/login` y `GET /users/me`.
- Modelos preparados para ejercicios de frases o texto compartido, elección
  múltiple o huecos, soluciones privadas y resultados de un intento.
- Configuración por variables de entorno y script PowerShell para generar
  secretos aleatorios en `.env`, excluido de Git.
- Pruebas automatizadas y workflow de GitHub para integración con una instancia
  separada de MongoDB. Se conservó la configuración previa de SonarQube.

### Decisiones técnicas y justificación

**Modelo documental.** Se implementó el diseño acordado previamente: cursos
y temas embebidos en cada asignatura porque el catálogo es acotado y se consulta
conjuntamente. Los historiales no se acumulan dentro de usuarios ni asignaturas:
un documento de `Tests` representará un test y un intento, con índice por alumno
y fecha. Esta decisión se apoya en las consultas previstas; no implica que un
modelo SQL sea incapaz de resolver el mismo problema.

**Referencias e integridad.** IDs principales y embebidos usan BSON `ObjectId`.
La aplicación comprueba unicidad global en el catálogo y pertenencia de niveles
y temas. MongoDB no proporciona aquí claves foráneas automáticas. El curso
escolar y el nivel MCER son campos diferentes. Las fechas se guardan en UTC.

**Acceso asíncrono.** Se utilizó `AsyncMongoClient` de PyMongo, conforme a la
especificación, con un cliente compartido durante el lifespan. Hash y
verificación de contraseñas se ejecutan en un hilo para evitar bloquear el
event loop. No fue necesario introducir transacciones multidocumento.

**Autenticación.** Se eligieron `pwdlib[argon2]` y PyJWT: contraseñas mediante
Argon2id y tokens de acceso HS256 de 30 minutos configurables. El servidor
verifica firma, expiración, tipos de claims y existencia del usuario. HTTPBearer
describe correctamente el contrato JSON de login en OpenAPI.

**Validación y privacidad de respuestas.** Los contratos públicos se separan de
los documentos privados. No se devuelve `password_hash`; las entradas rechazan
campos adicionales y los errores 422 excluyen la entrada recibida. Registro y
login normalizan el email de la misma manera. El índice único y la captura de
`DuplicateKeyError` cubren registros concurrentes. CORS usa orígenes explícitos.

**Persistencia y seed.** El volumen con nombre conserva MongoDB al recrear
contenedores. El usuario del backend tiene `readWrite` únicamente en su BD;
la cuenta administrativa se reserva para administración. El seed utiliza IDs
estables y `$setOnInsert` para conservar cambios manuales y evitar duplicados.
No funciona como migración ni crea alumnos con contraseñas públicas.

**Pruebas aisladas.** Se utilizó MongoDB real para comprobar índices,
autenticación y persistencia. Otro proyecto Compose aporta red y almacenamiento
efímero independientes. Cada prueba crea su propia BD `trainer_test_<uuid>`.
Los permisos amplios necesarios para crearlas y eliminarlas se limitan a esa
instancia de pruebas, sin afectar al usuario de aplicación en desarrollo.

**Dependencias.** Se fijaron versiones de FastAPI, Uvicorn, PyMongo, Pydantic,
pydantic-settings, pwdlib, PyJWT y email-validator. Las dependencias de desarrollo
incluyen pytest, pytest-cov, HTTPX y Ruff. Sus versiones están en los archivos
de requisitos; Python y las imágenes externas también tienen versiones fijadas.

### Archivos/componentes principales

- `docker-compose.yml`, `compose.test.yml` y `docker/mongo-init.js`.
- `backend/Dockerfile` y `backend/Dockerfile.test`.
- `backend/app/main.py`, `config.py`, `database.py` y `security.py`.
- `backend/app/models.py`, `schemas.py` y `seed.py`.
- `backend/tests/conftest.py`, `test_auth.py` y `test_models.py`.
- `backend/requirements.txt`, `requirements-dev.txt` y `pyproject.toml`.
- `.env.example`, `.gitignore` y `scripts/setup-env.ps1`.
- `.github/workflows/backend-tests.yml` y `README.md`.

### Problemas encontrados y soluciones

- Docker no estaba instalado. Se instaló Docker Desktop 4.94.0 por usuario,
  con autorización del autor, y se comprobó el motor Linux con WSL. Se añadió
  su directorio de binarios al PATH de las sesiones de comprobación.
- Python dentro del contenedor no reconocía la CA de la conexión a PyPI. Se
  exportó el bundle de CAs de confianza del equipo y se suministró como secreto
  opcional de BuildKit. HTTPS siguió verificándose; el bundle y los overrides
  locales se conservaron fuera de Git y el bundle no se incorporó a la imagen.
- Registro y perfil devolvían fechas con precisiones distintas: Python usa
  microsegundos y BSON Date conserva milisegundos. Se ajustó la fecha al crear
  el usuario para que ambas respuestas coincidan con el dato persistido.
- El usuario inicial de pruebas podía leer/escribir, pero no eliminar BDs.
  Se añadió `dbAdminAnyDatabase` exclusivamente en la instancia efímera de
  pruebas, manteniendo los permisos limitados del backend de desarrollo.
- Algunas formas incorrectas de claims JWT pueden provocar errores de tipo
  en la librería. Se convirtió ese caso en 401 y se probaron claims con valores
  nulos, objetos y arrays, además de tipos y firmas incorrectos.

### Validación ejecutada en la entrega

- Ambos archivos Compose validados con `config --quiet`; imágenes construidas
  y servicios locales arrancados.
- **65 pruebas correctas**: 39 de integración y 26 de modelos/configuración.
  Cobertura del backend: **96 %**.
- Ruff, comprobación de formato y `git diff --check` correctos.
- Seed ejecutado dos veces. Las pruebas comprobaron también que preserva IDs,
  número de documentos y cambios manuales.
- Contenedores de desarrollo recreados sin eliminar el volumen: mismo
  catálogo e IDs, sin volver a ejecutar el seed entre consultas.
- `/health`: 503 con MongoDB detenido y 200 tras arrancarlo.
- mongo-express: 401 sin credenciales y 200 con las configuradas.
- Logs de desarrollo revisados sin encontrar los secretos locales.

Quedó un aviso de deprecación de Starlette sobre HTTPX en TestClient; no se
ocultó. El workflow remoto de GitHub no se ejecutó en esta entrega. La instancia
de pruebas se retiró al terminar y los servicios de desarrollo quedaron activos.
No se hicieron commits, pushes ni despliegues.

### Límites y siguientes entregas

No se incorporaron pantallas de Vue, endpoints de ejercicios, llamadas a IA ni
dashboard. `Tests` permanece vacío y las plantillas están inactivas. El modelo
valida estructura y vínculos de huecos, pero no garantiza calidad pedagógica.
La autenticación local aún no incluye refresh tokens, verificación de email,
recuperación de contraseña, limitación de intentos ni revocación al cerrar sesión.

El siguiente incremento previsto es conectar Vue con registro, login y perfil;
después, resolver y persistir un test fijo antes de añadir generación con IA.

### Impacto en la memoria del TFG

Esta entrega aporta material para diseño del modelo documental, arquitectura
del backend, contratos de API, seguridad, reproducibilidad del entorno y
estrategia de pruebas. Las justificaciones pueden apoyarse en el flujo de
consulta y en las comprobaciones realizadas. No se han medido mejoras de
rendimiento ni evaluado la eficacia pedagógica del material inicial.

## 2026-10-07 — Reglas de trabajo y trazabilidad del TFG

### Objetivo e implementación

A petición del autor, se guardaron en `AGENTS.md` las trece secciones de reglas
proporcionadas: Git solo para consulta, commits y pushes manuales, desarrollo
incremental, seguridad, comprobaciones y registro técnico. Se creó este devlog
con la entrega anterior y se reservó `docs/decisions/` para futuros ADR cuando
una decisión significativa necesite documentación independiente.

### Justificación e impacto en la memoria

Separar instrucciones estables y registro técnico evita que `AGENTS.md` crezca
como un diario y conserva información útil para redactar la memoria. Los ADR
son opcionales para decisiones importantes; no se creó ninguno en esta tarea.

### Comprobaciones

Se revisó la correspondencia de `AGENTS.md` con el bloque proporcionado y se
comprobó el formato de los cambios con `git diff --check`. Esta tarea solo
añade documentación; no modifica el backend ni vuelve a ejecutar sus pruebas.

## 2026-10-07 — Revisión de cierre de la primera entrega

### Objetivo y alcance

Contrastar código, configuración, MongoDB y pruebas antes de iniciar Vue.
No se hicieron cambios funcionales, no se modificó el README ni `.gitignore`,
no se crearon ADR y no se ejecutaron operaciones de Git que alteren el índice,
el historial o el remoto. Se amplió exclusivamente este registro entre los
archivos del proyecto; los informes auxiliares quedaron en `.tools/`, ignorado.

### Estructura y contratos revisados

- `backend/app/main.py`: factory de FastAPI, lifespan, cinco rutas, dependencia
  de usuario autenticado y manejo de errores. Sigue siendo un módulo pequeño;
  separar routers se puede valorar cuando crezcan las funcionalidades.
- `config.py`: variables de entorno, secretos protegidos en representaciones
  y errores de configuración, duración del JWT y orígenes CORS explícitos.
- `database.py`: un cliente PyMongo asíncrono por lifespan y creación repetible
  de las cinco colecciones e índices.
- `models.py`: documentos privados, BSON ObjectId, fechas UTC y validaciones de
  catálogo, actividades y consistencia de tests. `schemas.py`: entradas y
  respuestas públicas separadas. `security.py`: Argon2id, hilos y JWT HS256.
- `seed.py`: datos iniciales y upserts conservadores; `backend/tests/`: fixtures
  y pruebas; `docker/`: inicialización de usuarios de MongoDB; `scripts/`:
  generación de `.env`; `.github/workflows/`: tests nuevos y SonarQube existente.

Registro valida primero el JSON y la selección académica, genera el hash,
inserta el alumno y responde 201 con el perfil. Un email duplicado responde 409
mediante el índice único, incluida la concurrencia. Login normaliza el email,
verifica la contraseña y responde con token, tipo Bearer y 1800 segundos por
defecto. Un usuario desconocido verifica contra un hash ficticio para evitar
una diferencia trivial de coste; las credenciales incorrectas responden 401.
Las entradas que no cumplen el contrato, también en login, responden 422.

El JWT contiene solo `sub` (ID del alumno), `iat` y `exp`. Está firmado, no
cifrado. `/users/me` verifica firma, algoritmo, claims, tiempos y existencia del
usuario, y devuelve exclusivamente su perfil público. No hay roles editables
por el alumno, refresh tokens ni revocación implementada.

### Diseño y estado real de MongoDB

Se inspeccionaron las colecciones e índices sin modificar datos de desarrollo.
En el momento de la revisión: `Usuarios` 0 documentos, `Asignaturas` 1,
`Prompts` 2, `BancoEjercicios` 1 y `Tests` 0.

- `Usuarios`: email, nombre opcional, hash, referencias académicas, MCER
  opcional y fechas. Índice único `email_unique`.
- `Asignaturas`: código estable, nombre y niveles con sus temas embebidos.
  Índice único `codigo_unique`.
- `Prompts`: referencia a asignatura, nombre, tipo, versión entera, variables,
  contenido, estado activo y fecha. Índice único `prompt_version_unique` en
  `(asignatura_id, nombre, tipo, version)`.
- `BancoEjercicios`: referencias a asignatura, nivel y tema; array de ejemplos
  completos. Índice único `tema_unique` en `tema_id`.
- `Tests`: referencias a alumno y catálogo; objetivo, presentación, texto,
  prompts y sus versiones, modelos de IA, preguntas privadas, respuestas,
  feedback, etiquetas, estadísticas, estado y fechas. Índice no único
  `historial_usuario` en `(usuario_id asc, fechas.generacion desc)`.

Todas tienen además el índice automático de `_id`. Niveles y temas son
subdocumentos, no colecciones independientes. La validación de pertenencia se
invoca en registro y seed; la unicidad global del catálogo se comprueba en seed
y lectura del catálogo. Los modelos de futuros tests no consultan por sí solos
la BD para resolver sus referencias.

**Límite importante:** no se configuraron validadores de esquema en MongoDB.
Los índices únicos sí se aplican en el servidor, pero las comprobaciones de
Pydantic pueden saltarse mediante escrituras directas. La inmutabilidad del
contenido de un prompt utilizado es una regla de diseño y del seed, no una
restricción impuesta por MongoDB. No existe todavía API de edición de prompts.

### Representación del seed y ampliación

El banco contiene **diez ejemplos, cada uno con una pregunta**; no es un test
de diez preguntas ya creado. Todos usan `objetivo=gramatica`,
`presentacion=frases_independientes`, `texto_base=null` y `formato=fill_blanks`.
Cada pregunta conserva un ID, enunciado con verbo de pista y solución con lista
de respuestas aceptadas y `sensible_mayusculas=false`. Los IDs de ejemplos son
ObjectId estables. El seed actual se centra en afirmativas de Present Simple.

El modelo admite también elección múltiple (2–8 opciones con solución que
referencia una opción) y texto compartido manual o generado. Los marcadores
`[h1]`, `[h2]` se vinculan con preguntas mediante `hueco_id`; comprensión
lectora puede usar preguntas sin huecos. El banco admite hasta 50 ejemplos y
cada actividad hasta 50 preguntas. La corrección, normalización de respuestas,
propiedad del test y transiciones persistidas aún deben implementarse en las
entregas de ejercicios. No se deben atribuir esas funciones al modelo actual.

Nuevos cursos y temas requieren IDs nuevos y referencias coherentes; nuevos
bancos se identifican por tema. El seed actual carga un catálogo concreto:
añadir contenido en su definición no actualiza un documento ya existente,
porque `$setOnInsert` conserva el original. La ampliación de catálogos existentes
necesitará una actualización explícita o migración.

### Docker y secretos

Desarrollo define `mongodb`, `backend` y `mongo-express` opcional (`tools`).
El volumen `mongodb_data` se monta en `/data/db`; el script de inicialización
se monta en solo lectura y se ejecuta únicamente al inicializar un volumen
vacío. Backend depende del healthcheck de MongoDB. Los puertos publicados
están limitados a 127.0.0.1:8000 y 127.0.0.1:8081. MongoDB no publica puerto
en el host. Compose de pruebas define `mongodb-test` y `tests`, con otra red y
`tmpfs` en `/data/db`, sin compartir el volumen de desarrollo.

Configuración: `MONGO_ROOT_USER`, `MONGO_ROOT_PASSWORD`, `MONGO_APP_USER`,
`MONGO_APP_PASSWORD`, `MONGODB_DB`, `JWT_SECRET`,
`ACCESS_TOKEN_EXPIRE_MINUTES`, `ALLOWED_ORIGINS` y, para la herramienta opcional,
`MONGO_EXPRESS_USER` y `MONGO_EXPRESS_PASSWORD`. `MONGODB_URI` es obligatoria para
Python; Compose la construye con el usuario de aplicación, ignorando el valor
local de esa variable en `.env`. La instancia interna no es accesible desde
Python en el host sin una configuración adicional de conexión.

Se comprobó que `.env`, `.env.*` (excepto `.env.example`) y `.tools/` están
excluidos de Git. Las credenciales fijas de `compose.test.yml` pertenecen solo
a pruebas efímeras. No son las de desarrollo y no deben reutilizarse.

Comandos de arranque y seed, desde la raíz del repositorio:

```powershell
# Si Docker se acaba de instalar, actualizar PATH en esta sesión.
$env:PATH = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;$env:PATH"
# Solo si .env todavía no existe:
.\scripts\setup-env.ps1
# Construcción en este equipo, usando su bundle local de CAs:
docker compose -f docker-compose.yml -f .tools/compose.ca.yml up -d --build --wait
docker compose exec backend python -m app.seed
# Arranque posterior con la imagen ya construida:
docker compose up -d --wait
# Herramienta opcional:
docker compose --profile tools up -d mongo-express
```

`docker compose down` conserva el volumen. La opción `-v` elimina sus datos y
no forma parte del flujo ordinario. Los overrides de CA son locales y no se
versionan; otro equipo puede construir directamente si su red no los requiere.

### Pruebas reejecutadas y cobertura exacta

Las **65 pruebas volvieron a pasar**, con el mismo aviso de Starlette/HTTPX.
Los casos parametrizados cuentan por separado; no son 65 funciones distintas.

| Grupo | Casos |
| --- | ---: |
| Registro, hash, perfil, duplicados, concurrencia, selección académica y límites | 16 |
| Credenciales inválidas de usuario existente/desconocido | 1 |
| Tokens ausentes, malformados, inválidos y usuario eliminado | 18 |
| Catálogo, OpenAPI, CORS y healthcheck | 2 |
| Seed repetido y colisiones globales de IDs | 2 |
| Catálogo inicial, tipos BSON e integridad académica en modelos | 2 |
| Estructura de actividades, lectura, opciones, huecos y ejemplos duplicados | 14 |
| Consistencia de test y fechas conscientes de zona horaria | 2 |
| Secretos y configuración CORS | 8 |
| **Total** | **65** |

Los primeros cinco grupos corresponden a 39 casos de integración y los últimos
cuatro a 26 casos de modelos/configuración. Integración usa MongoDB real,
aunque el caso de healthcheck no disponible simula el error mediante monkeypatch.
La parada real de MongoDB ya se verificó manualmente en la entrega inicial;
no se volvió a detener desarrollo en esta revisión.

Se ejecutó la suite con `--cov=app`, sin cobertura de ramas. El informe cuenta
**458 de 479 líneas ejecutables cubiertas: 95,615866 %, mostrado como 96 %**.
Quedan 21 líneas sin ejecutar:

| Módulo | Cubiertas / ejecutables | Pendiente de la suite |
| --- | ---: | --- |
| `config.py` | 31 / 32 | URI MongoDB inválida |
| `database.py` | 18 / 20 | Excepción al crear una colección concurrentemente |
| `main.py` | 96 / 99 | Fallo de MongoDB en lifespan y handler general 503 |
| `models.py` | 219 / 224 | Cinco rechazos: hueco sin ID, aciertos sobre total, respuestas incoherentes, estadísticas prematuras y resolución anterior a generación |
| `schemas.py` | 33 / 33 | Sin líneas pendientes; no implica probar todas las combinaciones |
| `security.py` | 24 / 24 | Sin líneas pendientes; no es una auditoría exhaustiva de seguridad |
| `seed.py` | 37 / 47 | Catálogo preexistente con otros IDs y entrada CLI con sus errores/cierre |

El rechazo de un hueco sin ID se prueba en un caso que falla antes por
incoherencia de marcadores; su validación específica no se alcanza. El seed CLI
sí se ejecutó manualmente, pero no dentro de la medición de pytest.

Esta cobertura mide ejecución de código Python de `app`, no porcentaje de
requisitos, ramas, Docker, scripts PowerShell/JavaScript, frontend, calidad
pedagógica, seguridad total ni rendimiento. La preservación de tests durante
el seed se comprueba con `Tests` vacío; no se prueba un intento histórico
completado no vacío. No hay type checker configurado. Ruff y su comprobación
de formato volvieron a pasar; ambos Compose son válidos y `/health` responde 200.
El workflow de GitHub/SonarQube no se ejecutó desde esta revisión.

El informe JSON queda localmente en `.tools/coverage-review.json`. La ejecución
utilizó los overrides locales de CA y de informe de cobertura. La instancia de
pruebas se retiró al terminar y no se modificaron los datos de desarrollo.

### Deuda técnica y puntos para cerrar antes de Vue

1. **Documentación y reglas:** el `AGENTS.md` de la raíz ya no está presente;
   existe `docs/AGENTS.md`, con una regla adicional de no modificar README.
   Para que las instrucciones cubran automáticamente todo el repositorio debe
   resolverse su ubicación antes de futuros trabajos. No se movió en esta revisión.
2. **Versionado del registro:** `.gitignore` excluye actualmente `docs/`, por lo
   que este devlog y `docs/AGENTS.md` no aparecerán en el commit ordinario.
   También excluye README, aunque README ya está versionado: una regla de ignore
   no deja de seguir un archivo existente. Se conservó esa configuración del autor.
3. **Guía de uso:** el README actual solo contiene el título. Los comandos
   relevantes quedan en este devlog sin modificar el archivo protegido.
4. **Reproducibilidad:** las dependencias directas están fijadas, pero no existe
   lock completo de dependencias transitivas ni imágenes externas por digest.
5. **Autenticación:** ausencia de limitación de intentos, renovación, revocación,
   recuperación y verificación de email; no impide el prototipo local, pero
   limita un despliegue público. Las respuestas 409 permiten conocer que un email
   está registrado. No se añadió rehash automático de contraseñas al hacer login.
6. **Datos y evolución:** referencias e inmutabilidad de prompts dependen de
   lógica de aplicación; deben reforzarse cuando haya nuevas escrituras.
   No hay migraciones ni motor de corrección/transiciones de tests.
7. **Testing:** caminos de error enumerados pendientes, cobertura de ramas no
   medida, warning de TestClient y CI remoto aún sin comprobar.
8. **Integración Vue:** confirmar el contrato de errores 422 (lista estructurada
   o mensaje simple para selección académica), email duplicado 409 y token 401.
   El primer cliente previsto guardará el token en memoria: recargar requerirá
   login. Debe manejar la expiración, consultar `/users/me` y usar IDs reales
   de `/asignaturas`; los orígenes deben coincidir con `ALLOWED_ORIGINS`.

### Decisión que justificaría un ADR

**Modelo documental del catálogo y de los intentos de test.** Afecta consultas,
límites de arrays, integridad referencial, preservación del historial y futuras
migraciones. Un ADR útil compararía la jerarquía embebida con colecciones
separadas y una alternativa relacional, y explicaría por qué se eligió un
catálogo acotado más documentos independientes por intento. La estrategia
definitiva de sesión podría justificar otro ADR cuando se decida persistencia
o cookies. No se creó un ADR para cada librería ni para decisiones menores.

### Impacto en la memoria

La revisión distingue funcionalidades implementadas, modelos previstos y
restricciones realmente impuestas por MongoDB. Aporta el desglose reproducible
de pruebas y una interpretación precisa de la cobertura, evitando equipararla
con cumplimiento de requisitos o validación pedagógica. Identifica también los
límites de seguridad y las condiciones necesarias para ampliar el sistema.

## 2026-10-08 — Diagnóstico y propuesta de ramas e integración continua

### Objetivo y alcance

El autor define dos ramas permanentes: `main` para código estable y `develop`
para desarrollo habitual. Representan estados del código; el TFG mantiene una
única entrega académica final. Esta revisión documenta una propuesta pendiente
de aprobación, sin crear ramas ni modificar workflows, configuración de Sonar,
código funcional, índice de Git, historial o repositorio remoto.

### Estado verificado

- La rama local activa y la rama predeterminada de GitHub son `main`.
  `origin` apunta a `https://github.com/agusbhb/tfg-english-app.git`.
  Solo existe `main` localmente y en el remoto consultado, en `fcca6c2`.
- El backend y `.github/workflows/backend-tests.yml` siguen sin estar
  versionados. El workflow de pruebas local ejecuta Docker/MongoDB aislado
  en pushes a `main` y en PR sin filtro de rama destino.
- El workflow remoto `sonarcloud.yml` ejecuta únicamente el scanner en pushes
  a `main` y en PR sin filtro de destino. No repite la suite de pytest.
- La API pública del proyecto Sonar confirma `sonar.autoscan.enabled=false`:
  el análisis automático está desactivado. El último análisis por CI de `main`,
  de 2026-09-25, corresponde al commit anterior y tiene Quality Gate OK;
  todavía no valida el nuevo backend.
- `sonar-project.properties` solo configura proyecto y organización.
  El contenedor de pruebas genera cobertura en consola, sin XML exportado
  para el scanner. No hay dependencia ni intercambio de informes entre workflows.
- Las consultas públicas no permiten confirmar el plan de la organización
  ni las reglas efectivas de protección de `main`; deberán revisarse en las
  interfaces correspondientes. `.gitignore` continúa excluyendo `docs/`:
  este registro se actualiza localmente y no entrará en un commit ordinario.

### Propuesta técnica pendiente de implementar

1. Mantener `main` como rama predeterminada de GitHub y como rama principal
   de Sonar. Trabajar habitualmente en `develop` e integrar cambios estables
   mediante PR `develop` -> `main`, conservando ambas ramas.
2. Consolidar la CI en un workflow con dos trabajos: pruebas y análisis Sonar.
   Ejecutar pruebas en pushes a `develop`/`main` y PR con destino `main`.
   El análisis dependerá de las pruebas y solo se ejecutará en pushes a `main`
   o PR internas dirigidas a `main`. Retirar el workflow independiente del
   scanner al consolidarlo para evitar análisis redundantes.
3. Ejecutar pytest una vez por evento, producir `coverage.xml` en formato
   Cobertura, extraerlo antes de retirar los contenedores y publicarlo como
   artefacto del mismo workflow. Sonar descargará ese informe y analizará el
   mismo checkout; no reutilizará cobertura de otro push ni repetirá las pruebas.
   Un push a `develop` con PR abierta puede provocar dos ejecuciones, porque
   el evento de PR valida también la integración propuesta con `main`.
4. Hacer portables las rutas de cobertura: conservar `backend/app` dentro de
   la imagen de pruebas y ejecutar desde su directorio padre, con
   `relative_files=true` y `source=["backend/app"]` en la configuración de
   Coverage.py. Usar `--cov` sin valor y `--cov-config` explícito para respetar
   esa configuración. Verificar la resolución de los archivos del XML frente
   al checkout del scanner, evitando rutas propias del contenedor `/app/app`.
5. Definir inicialmente `sonar.sources=backend/app`, `sonar.tests=backend/tests`
   y `sonar.python.coverage.reportPaths=coverage.xml`, con Python 3.12 y UTF-8.
   El alcance será el backend actual; deberá ampliarse cuando se incorpore Vue.
   Mantener la medición actual de líneas, sin introducir cobertura de ramas
   ni equiparar la cobertura de código nuevo en Sonar con el 96 % global de pytest.
6. Mantener análisis basado en CI y activar `sonar.qualitygate.wait=true`
   para que un Quality Gate fallido haga fallar el trabajo de análisis.
   Conservar acciones fijadas por SHA y limitar permisos a lectura del código.
   El token de Sonar solo se proporcionará al trabajo de análisis; las pruebas
   seguirán utilizando credenciales efímeras de la instancia MongoDB aislada.
7. Proteger `main` mediante PR y comprobaciones de pruebas/calidad, sin exigir
   aprobaciones de terceros en un proyecto con un único autor. Proteger ambas
   ramas contra eliminación y force push, permitiendo el desarrollo ordinario
   en `develop`. Preferir merge commits para conservar la ascendencia entre
   ramas permanentes; sincronizar posteriormente `develop` con `main` cuando
   proceda. Todas las operaciones Git y ajustes externos los realizará el autor.

### Justificación y límites

La configuración base no exige análisis independiente de `develop`. La
documentación actual de SonarQube Cloud limita el plan Free al análisis de la
rama principal y PR hacia ella; OSS, Team y Enterprise permiten analizar otras
ramas. Que el repositorio sea público no demuestra por sí solo el plan activo.
Si se confirma un plan compatible, analizar `develop` podrá añadirse después.
No se enviará el código de `develop` como si fuera `main`.

Dos trabajos dentro del mismo workflow permiten compartir el informe mediante
un artefacto y `needs`, manteniendo separadas las comprobaciones y el acceso al
token. Se evita la complejidad de descargar informes entre workflows distintos.
Las PR desde forks pueden ejecutar pruebas sin secretos, pero quedan fuera del
análisis con token de esta propuesta; no se introduce `pull_request_target`.

Antes de exigir los checks en GitHub, deberán ejecutarse y comprobarse sus
nombres reales. En Sonar habrá que confirmar el plan, el vínculo con GitHub,
la rama principal y el token `SONAR_TOKEN`. El éxito del scanner anterior no
demuestra que la cobertura se haya importado: la futura validación comprobará
la lectura del XML, ausencia de archivos sin resolver y métricas del backend.

Referencias oficiales consultadas: [planes de SonarQube Cloud](https://docs.sonarsource.com/sonarqube-cloud/administering-sonarcloud/managing-subscription/subscription-plans),
[cobertura Python](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/test-coverage/python-test-coverage),
[integración con GitHub Actions](https://docs.sonarsource.com/sonarqube-cloud/analyzing-source-code/ci-based-analysis/github-actions-for-sonarcloud),
[artefactos entre trabajos](https://docs.github.com/en/actions/tutorials/store-and-share-data).

### Impacto en la memoria del TFG

Permite justificar separación entre desarrollo y código estable, validación
automática antes de integración, trazabilidad de cobertura por revisión y
adaptación de las herramientas de calidad al plan disponible. No representa
nuevas entregas académicas ni un despliegue automático a producción.

## 2026-10-08 — Implementación de CI unificada y cobertura portable

### Objetivo

Implementar la propuesta aprobada de validación para `develop` y `main`,
manteniendo SonarQube Cloud compatible con Free y análisis basado en CI.
La organización del TFG sigue correspondiendo a una única entrega final.

### Decisiones implementadas

- `.github/workflows/backend-tests.yml` es el único workflow, denominado
  `Backend CI`. Ejecuta `Backend tests` en pushes a `develop` y `main`, y PR
  hacia `main`. Se retiró `sonarcloud.yml` para evitar scanners independientes.
- El trabajo `SonarQube` depende de las pruebas y se ejecuta únicamente en
  pushes a `main` o PR hacia `main` originadas en el mismo repositorio.
  No analiza `develop` como rama independiente. Las PR desde forks ejecutan
  pruebas sin exponer `SONAR_TOKEN`; su análisis con secretos queda fuera
  del flujo actual.
- Las pruebas se ejecutan una vez por evento con MongoDB aislado, conservando
  `REQUIRE_MONGODB_TESTS=1` y la propagación del código de salida de pytest.
  La limpieza se ejecuta también cuando falla un paso del trabajo.
- Pytest-cov genera `coverage.xml`, que se extrae del contenedor antes de
  eliminarlo y se publica como artefacto `backend-coverage`, retenido siete
  días. El trabajo de Sonar descarga ese artefacto de la misma ejecución,
  sin volver a ejecutar pytest ni recuperar informes de otros commits.
- La imagen de pruebas conserva `backend/` bajo `/workspace` y ejecuta pytest
  desde `/workspace`. Coverage.py usa `relative_files=true` y
  `source=["backend/app"]`; `--cov` sin valor respeta esa configuración.
  No se cambió la medición de líneas a cobertura de ramas.
- `scripts/check_coverage.py` comprueba que el XML tenga archivos y líneas,
  que la fuente sea `backend/app` y que cada archivo exista dentro de esa
  carpeta. Rechaza rutas externas y archivos inexistentes. Se ejecuta en los
  dos trabajos, antes de publicar cobertura y antes de analizarla.
- Sonar conserva proyecto `agusbhb_tfg-english-app`, organización `agusbhb`
  y la acción existente. Se delimita el código a `backend/app` y los tests a
  `backend/tests`, con Python 3.12, UTF-8 e importación de `coverage.xml`.
  `sonar.qualitygate.wait=true` hace que un Quality Gate fallido provoque
  un fallo del trabajo de análisis. El alcance deberá ampliarse al añadir Vue.
- Los dos trabajos tienen permisos de lectura del código, checkout sin
  persistir credenciales y tiempos máximos de ejecución. Sonar conserva
  `fetch-depth: 0`. Las acciones se fijan por SHA; `SONAR_TOKEN` solo se pasa
  al paso del scanner. No se añadieron secretos de aplicación al workflow.
- `.gitignore` ahora excluye `docs/*` con excepción de `docs/TFG_DEVLOG.md`.
  El registro puede versionarse; otros documentos locales siguen excluidos.
  `AGENTS.md` permanece en la raíz, sin cambios y excluido de Git, al igual
  que `.env`, `.tools/`, caches e informes de cobertura. `.env.example`
  se mantiene como plantilla versionable sin credenciales reales.

### Comprobaciones locales realizadas

| Comprobación | Resultado |
| --- | --- |
| Workflow: sintaxis y expresiones | Actionlint 1.7.12, sin errores |
| Configuración Compose de pruebas y override local de CA | Válida |
| Construcción de imagen de pruebas y suite con MongoDB real aislado | 65 casos aprobados, cero omitidos |
| Cobertura de líneas del backend | 458/479, 95,615866 %, 96 % redondeado |
| Informe XML exportado del contenedor | Generado en la raíz como `coverage.xml` |
| Resolución del informe | Ocho archivos bajo la fuente relativa `backend/app`, comprobados también en el repositorio Windows |
| Validador frente a informes inválidos | Rechaza fuente absoluta, ruta fuera del backend, archivo inexistente e informe vacío |
| Ruff del backend y del validador | Sin errores |
| Formato | 11 archivos Python del backend y un validador ya formateados |
| Archivos candidatos a versionado | Sin coincidencias con las contraseñas/JWT locales ni los patrones de tokens o claves privadas comprobados |
| Git | Rama activa `main`, sin operaciones sobre índice, ramas, historial o remoto |

El XML contiene `<source>backend/app</source>` y nombres como `main.py`;
su combinación resuelve `backend/app/main.py` desde la raíz del checkout.
No contiene rutas absolutas propias del contenedor. La comprobación demuestra
compatibilidad local de rutas; no demuestra aún importación remota por Sonar.

Ruff del backend se ejecuta desde su directorio, donde resuelve correctamente
sus reglas por archivo y sus imports locales. El validador se revisa aparte
con la misma configuración. El primer intento desde la raíz con `--config`
explícito produjo avisos por cambiar ese contexto; se corrigió la invocación
sin modificar el código de aplicación ni los tests.

La suite conserva el aviso conocido de Starlette/TestClient sobre HTTPX.
La construcción local utilizó el secreto opcional de certificados y el
override de `.tools/compose.test.ca.yml`; no se incorporan a Git ni al workflow
remoto. Actionlint se descargó en `.tools/` y se verificó su checksum oficial.
La instancia y red del proyecto temporal `trainer-ci-check` se retiraron al
terminar; no se utilizaron ni modificaron las bases de datos de desarrollo.

### Validación remota pendiente

No se publicó código ni se ejecutó este workflow en GitHub Actions. Permanecen
pendientes la transferencia real del artefacto entre trabajos, importación del
XML por el scanner, actualización de métricas, resultado del Quality Gate y
decoración de una PR hacia `main`. El último análisis remoto anterior sigue
sin certificar esta implementación.

El autor deberá publicar la implementación en `main`, comprobar `Backend CI`
y Sonar, y crear/publicar `develop` manualmente. En `develop` comprobará que
las pruebas se ejecutan y Sonar se omite; en una PR hacia `main`, que ambos
trabajos se ejecutan. Solo después configurará protecciones obligatorias de
`main`, usando los nombres reales de las comprobaciones y el Quality Gate.
Deberá confirmar `SONAR_TOKEN`, análisis automático desactivado, rama principal
`main` y vínculo del proyecto con GitHub. No se cambiaron ajustes externos.

### Archivos principales e impacto en la memoria

`backend-tests.yml`, retirada de `sonarcloud.yml`, `sonar-project.properties`,
`backend/Dockerfile.test`, `backend/pyproject.toml`, `scripts/check_coverage.py`
y `.gitignore`. No se modificó el README ni se creó un ADR.

La implementación justifica validación previa a integración, separación de
pruebas y análisis, trazabilidad de cobertura por ejecución y compatibilidad
con las restricciones del plan disponible. Distingue resultados reproducidos
localmente de garantías que todavía requieren una ejecución remota real.

## 2026-10-09 — Primer frontend funcional: registro y sesión

### Objetivo y alcance

Completar Register -> Login -> Bienvenida -> Logout con Vue 3, Vite y Vue
Router, usando el contrato existente de FastAPI y MongoDB. No se implementan
dashboard, ejercicios, recuperación de contraseña ni nuevas rutas de backend.
No se modifican README, AGENTS, ramas, índice, historial ni ajustes remotos;
no se crea ADR.

### Arquitectura y decisiones

Se utiliza JavaScript con módulos y Composition API: el incremento tiene tres
vistas y un estado de sesión pequeño; no existe un frontend TypeScript previo
ni modelos compartidos que justifiquen añadir esa capa ahora. El contrato se
centraliza y se comprueba mediante pruebas. Vue Router 4 es compatible con Vue
3 sin introducir Pinia; la sesión usa reactividad nativa y estado de solo lectura
expuesto a las vistas. No se incorporan bibliotecas de componentes ni fuentes
remotas. Las dependencias npm se fijan y se conserva `package-lock.json` para
`npm ci`; Node 24 se utiliza en construcción local y CI.

| Carpeta/módulo | Responsabilidad |
| --- | --- |
| `frontend/src/main.js`, `App.vue` | Arranque, marco de página y reacción al cierre/expiración de sesión |
| `frontend/src/router.js`, `sessionGuard.js` | Rutas públicas, bienvenida privada y protección de navegación |
| `frontend/src/api.js` | Fetch, JSON, Authorization, timeout de 15 segundos y traducción central de errores |
| `frontend/src/auth.js` | JWT en memoria, perfil, expiración, logout y confirmación de registro |
| `frontend/src/views/` | Formularios de registro/login y bienvenida con consulta de perfil |
| `frontend/src/styles.css` | Presentación responsive, estados y foco visible |
| `frontend/tests/` | Contrato HTTP, sesión, navegación y comportamiento de las vistas |
| `frontend/scripts/prepare-coverage.js` | Comprobación y preparación portable de las rutas LCOV |
| `frontend/Dockerfile`, `nginx.conf` | Construcción Vite, servicio estático, rutas SPA y proxy de API |

### Contrato de autenticación

- Registro consulta `/asignaturas`, usa `id` y `niveles[].nivel_id` recibidos
  del servidor y limpia la selección de curso al cambiar de asignatura.
  Envía únicamente `nombre`, `email`, `password`, `asignatura_id` y `nivel_id`
  a `/auth/register`. Conserva los espacios de la contraseña, exige los límites
  de 12-128 caracteres y valida nombre, email y selección académica.
- Registro correcto redirige a login con confirmación. Catálogo vacío,
  fallo de conexión, validaciones 422 y email duplicado 409 tienen mensajes
  comprensibles. Se puede reintentar cargar el catálogo.
- Login envía email/contraseña por JSON a `/auth/login`. El token solo se
  conserva en una variable privada del módulo, sin localStorage, sessionStorage
  ni cookies. La contraseña del formulario se borra al finalizar el intento.
- Bienvenida consulta `/users/me` con Bearer y muestra nombre/email devueltos
  por el servidor. Permite reintentar fallos temporales y cerrar sesión.
- Guards y estado reactivo bloquean contenido privado sin sesión; un 401 de
  perfil o la expiración según `expires_in` borran token/perfil y vuelven al
  login. La API sigue siendo la autoridad sobre validez del JWT. Las respuestas
  tardías de login/perfil no pueden restaurar una sesión que ya se ha cerrado.
- Recargar o cerrar la página pierde la sesión. Logout borra el estado local;
  no revoca un JWT que se hubiera copiado fuera del navegador antes del cierre.

### Docker y CORS

Compose incorpora `frontend`, servido por Nginx en `127.0.0.1:5173`, después
de que el backend esté saludable. `/api/` se envía al servicio interno
`backend:8000`; el navegador no utiliza nombres de contenedores ni credenciales
de MongoDB. Las rutas Vue usan fallback a `index.html`, también al recargar.
Vite en desarrollo local utiliza el mismo prefijo con proxy a puerto 8000.

Se revisó CORS: backend, Compose y `.env` local ya permiten únicamente
`http://localhost:5173` y `http://127.0.0.1:5173`; se conservó esa configuración
sin ampliar orígenes. Se verificó preflight permitido y origen externo denegado.
MongoDB conserva `mongodb_data`, puerto interno y autenticación. Se comprobó
que backend y mongo-express siguen arrancando. Las pruebas usan proyectos,
redes e instancias MongoDB temporales aisladas, sin volumen de desarrollo.

La imagen frontend copia solo el resultado de Vite a Nginx. `.dockerignore`
excluye `.env*`, dependencias locales, build e informes; `.gitignore` excluye
`node_modules`, `frontend/dist` y `frontend/coverage`. La red de este equipo
requiere certificados locales: se extendió el override ignorado
`.tools/compose.ca.yml` con el secreto opcional `npm_ca_bundle`, sin desactivar
TLS ni versionar certificados. Otros equipos pueden construir sin ese override.

### CI y Sonar: configuración local implementada

El workflow se denomina `TrAIner CI`, conservando `backend-tests.yml`. Mantiene
los eventos de pushes a main/develop y PR hacia main. Añade `Frontend checks`
con Node 24, `npm ci`, ESLint, Prettier, 31 pruebas con cobertura y build.
El trabajo backend conserva sus 65 pruebas y XML independiente.

Sonar depende de ambos trabajos, descarga sus artefactos de la misma ejecución
y analiza `backend/app,frontend/src`, con tests en carpetas separadas. Mantiene
la organización, proyecto, token, scanner, análisis basado en CI y condición
compatible con Free: main y PR internas hacia main. No repite suites ni analiza
develop independientemente.

Python sigue importando `coverage.xml`. JavaScript/Vue importa
`frontend/coverage/lcov.info`. El script LCOV transforma exclusivamente los
nombres `SF:src/...` a `SF:frontend/src/...` y comprueba su existencia; no altera
líneas, aciertos ni contadores. Se incluyen todos los JS y Vue de `src`, incluido
el arranque no cubierto; no se excluyen archivos para elevar porcentajes. Se
comprobaron los nueve archivos LCOV y los ocho XML contra el checkout local.
Los porcentajes globales y de código nuevo que muestre Sonar deberán verificarse
en remoto y no se equiparan automáticamente a los informes de pytest/Vitest.

### Comprobaciones locales realizadas

| Comprobación | Resultado |
| --- | --- |
| Backend con MongoDB real aislado | 65 casos aprobados, ninguno omitido |
| Ruff y formato del backend | Correctos: 11 archivos Python |
| Cobertura Python, sin ramas | 458/479 líneas, 95,615866 %, 96 % redondeado |
| Frontend: API / sesión / vistas | 11 + 12 + 8 = 31 pruebas aprobadas |
| ESLint y Prettier del frontend | Correctos |
| Cobertura de líneas de todo `frontend/src` | 162/165 = 98,18 % |
| Cobertura de ramas del frontend | 171/174 = 98,27 % |
| Build Vite y build Docker | Correctos; JS 101,79 kB, 38,95 kB gzip |
| Workflow y configuración Compose | Actionlint sin errores; Compose válido |
| Auditoría de candidatos y exclusiones | Sin secretos locales detectados; archivos locales excluidos |

Las pruebas de API del frontend comprueban payloads, ausencia de cookies,
Bearer en perfil, catálogo dinámico, 401/409/422/503, red y respuesta inesperada.
Las de sesión comprueban login/logout, recarga, timer de expiración, 401,
reintento, login incompleto/fallido, respuestas tardías y guards. Las de vistas,
con Vue Test Utils y happy-dom, prueban el flujo completo, cambio de asignatura,
catálogo vacío/no disponible, registro duplicado, credenciales incorrectas,
perfil inválido/no disponible y redirección al caducar. Se conserva el aviso
conocido de Starlette/TestClient sobre HTTPX en la suite backend.

Además se ejecutó un navegador Edge real sin interfaz contra otra instancia
temporal de FastAPI y MongoDB, con el frontend construido servido por Nginx.
Se verificaron registro, login incorrecto/correcto, perfil, logout, recarga,
duplicado, token alterado rechazado por FastAPI, ausencia de almacenamiento
persistente y CORS. Se inspeccionaron capturas de escritorio y móvil de 390 px:
sin desbordamiento horizontal ni errores JavaScript. El usuario de prueba
existió solo en MongoDB temporal en memoria; los proyectos temporales se
retiraron. Esta comprobación de navegador no está incorporada a la CI.

### Resumen de las 65 pruebas existentes del backend

Los casos parametrizados cuentan por separado; no son 65 funciones. El archivo
`tests/conftest.py` proporciona fixtures de MongoDB autenticado, base aleatoria
por prueba, seed y TestClient, pero no contiene casos adicionales.

| Archivo | Funcionalidad | Casos |
| --- | --- | ---: |
| `backend/tests/test_auth.py` | Registro persistente, normalización, Argon2, duplicados, concurrencia, entradas inválidas sin reflejar contraseña, pertenencia del nivel y límites de contraseña | 16 |
| `backend/tests/test_auth.py` | Igualdad del rechazo de credenciales para usuario conocido/desconocido | 1 |
| `backend/tests/test_auth.py` | Token ausente/malformado, 16 variantes firmadas inválidas y token de usuario eliminado | 18 |
| `backend/tests/test_auth.py` | Catálogo, cinco colecciones, OpenAPI, CORS y health disponible/no disponible | 2 |
| `backend/tests/test_auth.py` | Seed idempotente, conservación de datos y detección de colisiones antes de escribir | 2 |
| **Subtotal integración** | **MongoDB real aislado; health no disponible simulado por monkeypatch** | **39** |
| `backend/tests/test_models.py` | Catálogo y banco inicial, BSON, IDs globales y pertenencia académica | 2 |
| `backend/tests/test_models.py` | Doce formas inconsistentes de actividad, lectura/cloze, opciones y ejemplos duplicados | 14 |
| `backend/tests/test_models.py` | Ciclo del test, estadísticas, pareja prompt/versión y fechas con zona horaria | 2 |
| `backend/tests/test_models.py` | Secretos cortos/marcadores/ausentes y cuatro orígenes CORS inválidos | 8 |
| **Subtotal modelos/configuración** | **Sin necesidad de MongoDB** | **26** |
| **Total** | | **65** |

Quedan 21 líneas Python contables sin ejecutar: URI Mongo inválida, error de
creación concurrente de colección, errores de Mongo durante startup/handler,
cinco rechazos específicos de modelos y caminos alternativos/CLI del seed.
La comprobación de conservación de `Tests` usa una colección vacía, no un
intento histórico completado. Los modelos de ejercicios se validan, pero todavía
no hay motor de tests ni funcionalidades de ejercicios. No se mide cobertura
de ramas Python, carga, rendimiento, calidad pedagógica, Docker o seguridad
exhaustiva. En frontend quedan sin cubrir el montaje de `main.js` y la validación
preventiva de nombre/selección académica en el submit; el resto de límites se
completa con validación nativa y del backend. Los porcentajes no equivalen a
porcentaje de requisitos ni demuestran ausencia de defectos.

### Arranque y prueba manual en Windows

En este equipo, con Docker Desktop instalado, desde PowerShell:

```powershell
Set-Location 'E:\ehu\OneDrive - UPV EHU\TFG\tfg-english-app'
$env:PATH = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin;$env:PATH"
docker desktop start
if (-not (Test-Path -LiteralPath .env)) { .\scripts\setup-env.ps1 }
docker compose -f docker-compose.yml -f .tools/compose.ca.yml --profile tools up -d --build --wait
docker compose exec -T backend python -m app.seed
```

El seed puede repetirse sin borrar datos existentes. En una red sin necesidad
de la CA local, usar `docker compose --profile tools up -d --build --wait`.
El override de CA es propio del equipo y no estará en un clon nuevo.

- Frontend: `http://localhost:5173`.
- API/OpenAPI: `http://localhost:8000/docs`.
- Mongo-express opcional: `http://localhost:8081`, con sus credenciales de `.env`.

Crear una cuenta con email nuevo y contraseña de 12-128 caracteres; seleccionar
Inglés y el curso recibido del catálogo; verificar la confirmación en login;
iniciar sesión y comprobar el nombre; cerrar sesión e intentar acceder a
`/bienvenida`. Recargar bienvenida también debe devolver al login. Un email
duplicado o contraseña incorrecta deben mostrar errores comprensibles.

`docker compose down` conserva MongoDB. No usar `down -v` para el cierre normal.
Al modificar código servido por Docker, reconstruir el frontend; `npm run dev`
permite desarrollo con Vite si se instala Node 24 y dependencias para ese sistema.

### Límites y comprobaciones remotas pendientes

La aplicación completa se dejó arrancada localmente con backend, frontend,
MongoDB y mongo-express. No se publicó código ni se modificaron configuraciones
externas. Permanecen pendientes una ejecución real de `TrAIner CI`, transferencia
de ambos artefactos, importación de XML/LCOV, Quality Gate y decoración de PR.
Solo después deberán configurarse checks obligatorios, incluyendo el nuevo
`Frontend checks`. El plan de Sonar debe confirmarlo el autor.

No hay refresh token, revocación, recuperación de contraseña ni limitación de
intentos; la sesión en memoria es deliberada para este incremento. JavaScript
no aporta comprobación estática de tipos. La prueba de navegador se hizo en Edge,
sin auditar todos los navegadores o accesibilidad completa. Node/Nginx usan tags
de imagen mantenidos, todavía sin fijación por digest; no es un despliegue público
ni una configuración de HTTPS. Estas limitaciones no se resuelven añadiendo
funcionalidades fuera del alcance acordado.

### Impacto en la memoria

Permite justificar consumo del contrato REST, carga dinámica del catálogo,
separación de HTTP/sesión/vistas, autenticación y navegación privada, aislamiento
de pruebas, proxy local, cobertura por lenguaje y validación incremental tanto
de lógica como de interacción real con FastAPI/MongoDB.

## 2026-10-14 — Cierre del primer frontend: contraseña y responsive

### Objetivo y cambios

Cerrar la revisión de Register, Login, Bienvenida y Logout sin incorporar nuevas
funcionalidades. Al iniciar, Git seguía en `main`, con el backend/frontend sin
versionar y los cambios locales de CI anteriores pendientes; se conservaron.

- Register deja de mostrar permanentemente los requisitos de contraseña.
  El aviso aparece al abandonar el campo con un valor inválido o intentar
  enviar, tanto por el evento HTML `invalid` como por el handler `submit`.
  Desaparece al corregir la contraseña; si vuelve a quedar inválida mientras
  se edita, espera otra salida del campo o intento de envío.
- Se mantienen `required`, `minlength=12` y `maxlength=128`, más comprobación
  explícita en submit para impedir peticiones inválidas aunque se eluda HTML.
  Se cuentan caracteres Unicode sin recortar espacios. No se altera el contrato
  ni la validación de FastAPI. El borrado de la contraseña tras una petición
  restablece el estado de interacción, evitando un aviso vacío involuntario.
- El error inline tiene `role=alert`, asociación condicional mediante
  `aria-describedby`, `aria-invalid` y borde de error. Al corregirlo se retiran
  esas asociaciones, sin dejar referencias a un elemento inexistente.
- En la revisión inicial no había desbordamientos con textos habituales, pero
  a 768 px la columna de introducción quedaba en 292 px. Hasta 900 px se usa
  ahora una columna centrada de hasta 430 px; se oculta la tarjeta decorativa.
  En escritorio se mantienen las dos columnas y el formulario de 430 px.
- Se limita la anchura del formulario, se permite contraer las columnas de
  selects y se envuelven nombres/emails largos en Bienvenida. Se conservan
  colores, tipografía y espaciado de la estética existente, sin ocultar
  desbordamientos mediante recortes de la página.

### Comprobaciones locales

Se añadieron siete casos de vistas: aviso diferido al blur, corrección y nueva
edición, invalid nativo, bloqueo de tres contraseñas inválidas (vacía, corta y
129 caracteres), y aceptación de los límites 12/128 conservando espacios.

| Comprobación | Resultado |
| --- | --- |
| Frontend: API / sesión / vistas | 11 + 12 + 15 = 38 pruebas aprobadas |
| Cobertura de líneas JS/Vue | 175/178 = 98,31 % |
| Cobertura de ramas JS/Vue | 186/189 = 98,41 % |
| ESLint / Prettier | Sin errores |
| Build Vite | Correcto; JS 102,19 kB, 39,08 kB gzip |
| Backend con MongoDB real aislado | 65 pruebas aprobadas, ninguna omitida |
| Cobertura backend, sin ramas | 458/479 = 95,615866 %, mostrado como 96 % |
| Ruff y formato del backend | Correctos: 11 archivos Python |

Se ejecutaron comprobaciones automatizadas en Edge real a las tres anchuras
solicitadas, con catálogo y sesión simulados para revisar interfaz sin crear
usuarios en la BD de desarrollo. Se inspeccionaron capturas de las vistas.

| Ventana | Formulario Register/Login | Distribución | Desbordamiento horizontal |
| --- | ---: | --- | --- |
| 375 px | 335 px | Una columna | Ninguno |
| 768 px | 430 px | Una columna centrada | Ninguno |
| 1440 px | 430 px | Dos columnas | Ninguno |

Las comprobaciones incluyen ausencia inicial del aviso, blur inválido, envío
con Enter/clic bloqueado por HTML, desaparición al corregir, Login, Bienvenida,
Logout y nombres/emails largos. No hubo controles fuera del viewport ni errores
JavaScript. Los casos de navegador y capturas quedan en `.tools/`, excluidos
de Git; los siete tests de vistas sí se incluyen en la suite habitual de CI.

El servidor de revisión temporal se reinició para evitar resultados de CSS
obsoleto: los cambios del host Windows no siempre disparan HMR en el contenedor.
Se verificó explícitamente que a 768 px formulario e introducción estén alineados
y midan 430 px. La instancia MongoDB aislada se retiró al terminar. Se reconstruyó
el frontend servido por Compose para que los cambios sean visibles en puerto 5173.

### Archivos y límites

Archivos de este ajuste: `frontend/src/views/RegisterView.vue`,
`frontend/src/styles.css`, `frontend/tests/views.test.js` y este registro.
No se cambiaron backend, dependencias, workflows, README ni reglas AGENTS.
No se crearon ADR, ramas, commits ni configuraciones remotas.

Se mantiene el aviso conocido de Starlette/TestClient. La cobertura no implica
validación visual de todos los navegadores: la revisión responsive se hizo en
Edge, y los tests DOM no miden geometría CSS. La integración real de estos cambios
en GitHub Actions y Sonar sigue pendiente de una publicación y ejecución remota
por parte del autor. Permanecen sin cubrir el montaje de `main.js` y parte de
la validación preventiva de nombre/selección académica en Register.
