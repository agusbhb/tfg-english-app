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

## 2026-11-03 — Segundo incremento: práctica y corrección determinista

### Objetivo e implementación

El alumno autenticado puede elegir un tema de su curso, preparar hasta diez
preguntas fill_blanks, revisarlas en un modal, entregar incluso respuestas
vacías y consultar el resultado persistido. No se incorporan LLM, nuevas
colecciones, dependencias, prompts, historial ni servicios cloud.

Se añaden tres rutas con `current_user`: POST `/tests`, GET `/tests/{test_id}`
y POST `/tests/{test_id}/respuestas`. Crear un test deriva asignatura y nivel
del perfil autenticado, valida la pertenencia del tema y consulta el banco con
las tres referencias. GET y entrega filtran por `_id` y `usuario_id`; un test
ajeno devuelve el mismo 404 que uno inexistente. Un ID mal formado devuelve 422.
Los identificadores de ruta se validan como strings hexadecimales y se convierten
a ObjectId dentro del endpoint: FastAPI no admite directamente el tipo BSON
arbitrario como parámetro de ruta, aunque los modelos privados sí lo admiten.

### Banco, copia y trazabilidad

`SolvedExample.uso_practica` vale false por defecto: no se asume que un futuro
ejemplo few-shot sea un ejercicio para alumnos. Los diez ejemplos originales
del seed se marcan true. Para bancos antiguos, el seed habilita únicamente
ejemplos con los IDs originales, sin marca previa y preguntas idénticas al
contenido inicial. No sobrescribe contenido editado ni una exclusión explícita.
No modifica documentos de Tests; se comprobó con un intento completado no vacío.

Se eligen, en orden del banco, hasta diez preguntas fill_blanks de ejemplos
habilitados y frases independientes. Se mantiene un único objetivo por intento:
el del primer ejemplo elegible. No hay aleatorización ni textos compartidos.
Sin ejercicios elegibles se devuelve 404 con un mensaje específico.

Cada pregunta y solución se copia profundamente a `preguntas_generadas` de Tests.
Los IDs se reasignan q1...qN para evitar colisiones entre ejemplos. Se guardan
`origen=banco` y `origenes_preguntas`: ID nuevo, banco, ejemplo e ID original.
Los nuevos campos tienen defaults compatibles con documentos previos. La copia
permite corregir aunque se edite o elimine el banco; esto se prueba expresamente.
La configuración de MongoDB sigue dependiendo de variables de entorno.

### Corrector y privacidad

`practice.grade` es una función sin acceso a MongoDB ni LLM: acepta preguntas
y respuestas, elimina espacios exteriores en ambos lados y utiliza casefold
solo si `sensible_mayusculas=false`. Compara con todas las alternativas aceptadas;
no altera espacios interiores, puntuación ni realiza corrección aproximada.
Vacío o solo espacios cuenta como incorrecto. Se exige exactamente una respuesta
por pregunta; omisiones, duplicados, desconocidos y campos como es_correcta
aportados por el cliente se rechazan con 422.

La persistencia valida el documento completado y realiza un reemplazo atómico
filtrado por propietario y `estado=pendiente`. Solo una entrega concurrente
puede completarlo; las demás reciben 409. No necesita transacciones ni un estado
intermedio en_evaluacion. Se almacenan respuestas originales, aciertos, total
y fecha de resolución; la puntuación pública es 10 * aciertos / total.

Los esquemas públicos enumeran campos permitidos; no serializan directamente
los documentos privados. Mientras esté pendiente se devuelven enunciados e IDs,
sin soluciones; tras completar, se incluyen alternativas aceptadas, respuestas
y corrección. El corrector no depende del origen, por lo que podrá recibir
preguntas fill_blanks generadas por IA con el mismo esquema, sin implementar
esa generación en este incremento.

### Frontend

Bienvenida incorpora Practicar inglés; `/practicar` filtra los temas del catálogo
por el perfil del alumno. `/tests/:id` carga el intento desde la API, muestra los
inputs sin required y permite revisar antes de entregar. El diálogo nativo
muestra recuento, preguntas, respuestas y pendientes; permite volver conservando
lo escrito o confirmar. No envía nada al abrirlo. Las acciones se deshabilitan
mientras se entrega. Un fallo de red conserva respuestas y permite reintentar;
un 409 vuelve a consultar el estado persistido.

El resultado muestra puntuación, aciertos, errores y soluciones por pregunta.
`auth.authenticated` facilita las nuevas peticiones sin exponer el token en el
estado ni persistirlo; gestiona 401 y descarta respuestas de sesiones cerradas.
Los mensajes de práctica distinguen 404/409/422 de errores de registro/login.
Se mantienen guards y expiración; recargar sigue perdiendo la sesión. No hay
autoguardado de borradores ni listado histórico. Dentro de una sesión, volver
desde la selección al test mediante Atrás recupera el resultado con GET.

### Archivos principales

- Backend: main.py, models.py, schemas.py, seed.py y nuevo app/practice.py.
- Frontend: api.js, auth.js, router.js, styles.css, WelcomeView.vue y nuevas
  vistas PracticeView.vue y TestView.vue.
- Pruebas: nuevo backend/tests/test_practice.py, nuevo
  frontend/tests/practice.test.js y ampliación de frontend/tests/api.test.js.

### Resultados locales reales

| Comprobación | Resultado |
| --- | --- |
| Backend | 82 pruebas aprobadas: 65 anteriores y 17 nuevas |
| Cobertura backend de líneas, sin ramas | 579/605 = 95,70 %, mostrado como 96 % |
| Ruff y formato | Correctos, 13 archivos Python |
| Frontend | 48 pruebas aprobadas: 38 anteriores y 10 nuevas |
| Cobertura frontend de líneas | 98,17 % |
| Cobertura frontend de ramas | 96,04 % |
| ESLint, Prettier, build Vite y build Docker | Correctos |
| Workflow | Actionlint correcto; no se modificó la configuración de CI |

Los nuevos casos cubren privacidad, autenticación, propietario, respuestas
inválidas/vacías, corrección sensible/insensible a mayúsculas, alternativas,
persistencia tras eliminar el banco, doble entrega concurrente, opt-in,
colisiones de IDs y seed repetido con un test completado. Vue prueba selección
del curso, confirmación/cancelación, respuestas vacías, resultados guardados,
ausencia de ejercicios, reintento y 401; el cliente HTTP comprueba URL, JSON,
Bearer y mensajes de práctica.

Se verificó además con Edge real, Nginx, FastAPI y MongoDB temporal aislado:
selección, modal, cancelación conservando respuesta, entrega de una respuesta
correcta y nueve vacías, puntuación 1/10 y recuperación del resultado al volver.
No hubo errores JavaScript ni desbordamiento de resultados a 375, 768 y 1440 px.
El primer intento de comprobación recargaba la aplicación al abrir la URL del
test y perdía el JWT en memoria; se corrigió el procedimiento a navegación SPA,
manteniendo la política de sesión acordada. No se interpreta esto como un fallo
de persistencia: la prueba de API también verifica el documento en MongoDB.

Los proyectos temporales trainer-increment-tests y trainer-increment-ui se
retiraron; las cuentas de navegador existieron solo en MongoDB temporal. Las
capturas/scripts auxiliares permanecen en .tools, excluidos de Git. Se reconstruyó
la aplicación normal y se ejecutó el seed, preservando datos existentes.
Compose avisó de contenedores de pruebas antiguos del proyecto principal:
no se eliminaron contenedores preexistentes ajenos a esta validación.

### Límites, ejecución y revisión manual

Con Docker Desktop activo, en este equipo:

```powershell
docker compose -f docker-compose.yml -f .tools/compose.ca.yml --profile tools up -d --build --wait
docker compose exec -T backend python -m app.seed
```

Sin la CA local, usar Compose sin el segundo archivo. Entrar en localhost:5173,
iniciar sesión, pulsar Practicar inglés, elegir Present Simple y Empezar test.
Completar alguna pregunta, abrir Revisar y entregar, comprobar el resumen y
volver para editar. Confirmar con huecos vacíos, revisar puntuación/soluciones,
volver a temas y pulsar Atrás para consultar el resultado guardado.

Quedan el aviso conocido de Starlette/TestClient y caminos de error de base
de datos, startup y CLI sin cubrir. No se ha hecho auditoría completa de
accesibilidad ni de todos los navegadores. La validación de CI, importación
XML/LCOV y Quality Gate en Sonar siguen pendientes de publicación y ejecución
remota. La selección no adapta dificultad ni excluye ejercicios ya realizados.

No se modificaron dependencias, README, configuración Git ni remotos. La rama
activa encontrada era develop y se mantuvo. No se crearon commits ni se hizo
staging. La estructura existente de CI y Sonar incluye automáticamente los
nuevos módulos y pruebas; no se realizaron cambios externos.

## 2026-11-03 — Un test pendiente por usuario y tema

### Regla y diseño

Se permite como máximo un documento con estado pendiente para cada pareja
usuario_id/tema_id. Otros temas y usuarios son independientes y pueden tener
sus propios pendientes. Los completados quedan fuera de esta restricción.
No se añade autoguardado, historial ni integración LLM.

`pending_user_topic_unique` es un índice único compuesto en esos dos campos,
con `partialFilterExpression={estado: pendiente}`. La comprobación previa en
POST /tests mejora el mensaje, pero la garantía concurrente depende del índice:
DuplicateKeyError se traduce a 409 y no genera otro documento.

Antes de crear el índice, la inicialización agrupa pendientes por usuario y
tema, conserva el más antiguo por fechas.generacion y _id y elimina únicamente
los duplicados que sigan pendientes. El filtro de eliminación también exige
estado pendiente para proteger un intento completado durante la migración.
La operación es idempotente y no cambia ni sobrescribe completados. Si otro
proceso antiguo crea duplicados durante la instalación del índice, su creación
fallará y el backend no arrancará sin la restricción; en la primera actualización
deben detenerse los escritores antiguos. Compose recrea el backend local.

### API y concurrencia

- GET `/tests/pendiente?tema_id=...`: autenticado, valida que el tema pertenece
  al curso del alumno y devuelve su pendiente usando el DTO público sin
  soluciones, o null si no existe. Se registra antes de la ruta dinámica por ID.
- POST `/tests`: devuelve 409 si ya hay pendiente, incluida la carrera entre
  peticiones que habían superado simultáneamente la comprobación previa.
- DELETE `/tests/{test_id}`: autenticado y limitado al propietario. El borrado
  es una única operación condicional por ID, usuario y estado pendiente.
  Devuelve 200 con descartado=true, 404 para inexistente/ajeno y 409 si el estado
  ya no permite descartar. Nunca borra por tema de forma indiscriminada.
- GET `/tests/{test_id}` continúa recuperando la misma copia de preguntas,
  sin crear documentos ni devolver respuestas parciales de un pendiente.

La entrega existente también utiliza una condición estado=pendiente. Si compite
con el descarte, o bien se elimina el pendiente, o bien queda completado y el
descarte falla; un resultado completado no se elimina. CORS incorpora DELETE,
manteniendo los mismos orígenes locales explícitos.

### Vue

Al cambiar el tema se consulta el pendiente. Mientras no haya respuesta válida,
se bloquea crear. Si existe se ofrecen Continuar test y Descartar y empezar otro.
Continuar navega a su ID y la vista existente consulta las mismas preguntas con
campos vacíos. No se restauran borradores.

Descartar abre un diálogo de confirmación. Cancelar no envía DELETE; confirmar
elimina el ID concreto y solo después solicita crear otro. Se bloquean acciones
mientras se procesa. Si otra pestaña crea un pendiente y POST devuelve 409, se
consulta de nuevo y se ofrece continuar. Los errores al consultar/eliminar se
muestran y permiten reintentar sin crear a ciegas. Las consultas tardías de un
tema anterior se ignoran mediante una versión local de selección.

El borrado y la creación son dos peticiones, no una transacción: si se descarta
correctamente pero falla preparar el nuevo test (red o banco sin ejercicios),
puede quedar sin pendiente. El usuario puede reintentar crear; el índice sigue
evitando duplicados. Nunca se sustituye otro pendiente sin nueva confirmación.

### Pruebas y comprobaciones locales

| Comprobación | Resultado |
| --- | --- |
| Backend | 87 pruebas aprobadas, cinco nuevas sobre las 82 previas |
| Cobertura backend, líneas sin ramas | 610/636 = 95,91 %, mostrado como 96 % |
| Frontend | 55 pruebas aprobadas, siete nuevas sobre las 48 previas |
| Cobertura frontend de líneas | 97,52 % |
| Cobertura frontend de ramas | 95,06 % |
| Ruff y formato Python | Correctos, 13 archivos |
| ESLint y Prettier | Correctos |
| Build Vite y Docker | Correctos |
| Workflow y diff | Actionlint y git diff --check sin errores |

Backend comprueba continuar con el mismo ID sin crear, descarte, protección de
completados, pendientes en temas distintos, separación por alumno, autenticación,
IDs inválidos, limpieza de duplicados anteriores e índice parcial. La prueba
concurrente fuerza cuatro peticiones a llegar a insert_one antes de liberar
ninguna: una crea y tres reciben 409 por el índice real. Otra prueba enfrenta
entrega con descarte y comprueba que un completado nunca desaparece. Se adaptó
el caso anterior de selección de banco para descartar su pendiente entre nuevas
creaciones, respetando la nueva regla.

Vue comprueba continuar desde cero, cancelación y confirmación del descarte,
orden DELETE antes de POST, fallos de descarte/consulta, conflicto de otra pestaña,
cambio rápido de tema y métodos HTTP autenticados. Edge real contra Nginx,
FastAPI y MongoDB temporal confirmó mismo ID/preguntas, borrador no restaurado,
cancelación sin borrado, nuevo ID tras descarte y entrega posterior. No hubo
desbordamiento a 375 px en esa comprobación. Los proyectos temporales de pruebas
y navegador se retiraron sin tocar la BD de desarrollo.

### Archivos, operación y límites

Backend: app/database.py, app/practice.py, app/main.py y tests/test_practice.py.
Frontend: src/api.js, src/views/PracticeView.vue, tests/api.test.js y
tests/practice.test.js. Se actualiza este devlog. No se cambian dependencias,
colecciones, modelos, README ni configuración remota.

La aplicación normal se reconstruyó para instalar la restricción y servir la
nueva interfaz. Para probar: iniciar un test, volver a elegir tema, seleccionar
el mismo y continuar; después volver al tema, cancelar un descarte y finalmente
confirmarlo. La nueva URL debe tener otro ID. Tras completar el test, seleccionar
el tema permite crear otro y DELETE del completado debe responder 409.

Continúan pendientes la ejecución remota de CI/Sonar y el aviso conocido de
Starlette/TestClient. No se promete recuperar respuestas parciales. Se conservó
develop como rama activa; no se realizaron staging, commits ni operaciones sobre
historial, ramas o remoto. Los cambios del incremento anterior siguen pendientes
de versionado y se han preservado.

## 2026-11-04 — Borrador de respuestas guardado únicamente por blur

### Objetivo y modelo

Guardar una respuesta al abandonar su campo y recuperarla al continuar el mismo
test pendiente. Se reutiliza el documento Tests y su _id, sin nuevas colecciones
ni creación de intentos durante el guardado. No hay debounce, throttle, intervalos,
guardado periódico, listeners de salida ni espera forzada al cerrar/navegar.

Se añade `respuestas_borrador`, una lista de DraftAnswer con pregunta_id y
respuesta_dada, independiente de respuestas_alumno y de su corrección. Su default
es una lista vacía para leer documentos anteriores sin migración. La validación
privada comprueba preguntas existentes, IDs no duplicados y ausencia de borrador
en completados. AnswerInput reutiliza DraftAnswer sin permitir es_correcta ni
campos adicionales; mantiene el límite de 10 000 caracteres y acepta vacío.

### Endpoint y actualización atómica

PATCH `/tests/{test_id}/borrador` usa current_user, comprueba propietario,
estado pendiente y pertenencia de pregunta_id. IDs inválidos o respuestas
inválidas devuelven 422; ajenos/inexistentes, 404; completados, 409. CORS añade
PATCH sin ampliar los orígenes locales.

La actualización utiliza un pipeline atómico de MongoDB: sobre el array actual,
filtra únicamente la respuesta del ID indicado y añade su valor nuevo. No
sustituye el documento ni las demás respuestas y no usa upsert. Los valores se
envuelven en $literal y los IDs no se interpolan como rutas de campos MongoDB.
Así se admiten textos que comiencen por '$' y se evitan colisiones con sintaxis
de operadores. matched_count permite repetir el mismo valor sin devolver un
error falso por falta de cambios. El endpoint devuelve solo la respuesta guardada.

La condición de escritura repite _id, usuario_id y estado pendiente: un guardado
que haya leído antes de la entrega no puede recrear el borrador después. GET
por ID y GET pendiente devuelven el borrador sin soluciones. Al entregar se
utilizan exclusivamente las respuestas recibidas en la entrega y se elimina
el campo del documento persistido mediante el reemplazo condicional existente.
El DTO público de un completado devuelve una lista de borrador vacía.

### Vue y eventos

TestView inicializa campos y referencia de último valor guardado desde el DTO.
Solo @blur solicita guardar: cargar, escribir, abrir el resumen o navegar por
código no inicia una petición de guardado. Un clic que abandone el input sí
puede provocar su blur normal. No hay lógica especial de salida de página.

Cada blur captura el valor exacto, conservando espacios. Las operaciones de la
misma pregunta se encadenan en una cola de promesas, sin temporizadores; al
ejecutarse se comparan con el último valor confirmado. Repetir un blur sin
cambios no envía otra petición. La cola evita invertir el orden de cambios
rápidos y permite volver al valor anterior mientras había otro guardado en curso.
Las preguntas distintas pueden guardarse independientemente.

La respuesta del servidor solo actualiza la referencia guardada: nunca reemplaza
lo que el alumno sigue escribiendo. Un fallo muestra un aviso discreto por
pregunta, sin deshabilitar el formulario; otro blur puede reintentar. No hay
reintentos automáticos. Se mantienen las protecciones de sesión y los 401 siguen
cerrando una sesión inválida. Se ignoran resultados de cargas de otro ID.

La entrega no espera las colas ni combina valores con el borrador: toma siempre
el contenido actual del formulario. Los trabajos que no han empezado se omiten
si el test está enviándose o ya no está pendiente; los que estaban en vuelo
quedan sujetos a la condición de estado en MongoDB. Sus respuestas tardías no
restauran inputs ni avisos tras mostrar el resultado.

La selección de temas ahora informa de que Continuar recupera las respuestas
guardadas. Continuar, descartar y completar conservan los endpoints y restricciones
de pendientes únicos del incremento anterior.

### Pruebas locales realizadas

| Comprobación | Resultado |
| --- | --- |
| Backend | 98 casos aprobados: 87 anteriores + 11 nuevos |
| Cobertura backend de líneas, sin ramas | 631/659 = 95,75 %, mostrado como 96 % |
| Frontend | 62 casos aprobados: 55 anteriores + 7 nuevos |
| Cobertura frontend de líneas | 97,47 % |
| Cobertura frontend de ramas | 92,79 % |
| Ruff / formato Python | Correctos, 13 archivos |
| ESLint / Prettier | Correctos |
| Build Vite / Docker | Correctos |
| Workflow / diff | Actionlint y git diff --check correctos |

Backend prueba guardar, modificar, vaciar, repetir, leer un documento antiguo,
recuperar el mismo ID, no crear documentos, conservar otras respuestas concurrentes,
textos con '$', aislamiento, autenticación, preguntas inválidas y completados.
La entrega se prueba con respuestas diferentes del borrador y confirma que el
campo desaparece. Una carrera aleatoria y otra forzada retrasan PATCH hasta
después de completar el test: el tardío recibe 409 y no modifica el resultado.

Vue comprueba ausencia de guardado por carga/escritura/navegación sin blur,
repetición de blur sin cambios, guardado de vacío, avisos y reintento, orden por
pregunta y entrega actual sin esperar un PATCH pendiente. El cliente HTTP
comprueba PATCH, URL, Bearer y payload de una sola pregunta.

Se ejecutó Edge real sobre Nginx, FastAPI y MongoDB temporal aislado: escribir
no genera PATCH, abandonar el campo sí; continuar recupera dos respuestas del
mismo test; un fallo simulado de guardado avisa pero permite entregar un valor
actual diferente del borrador. Se verifican la corrección de ese valor, borrador
público vacío tras completar, PATCH posterior rechazado con 409 y ausencia de
desbordamiento a 375 px. Los proyectos temporales se retiraron sin modificar
datos de desarrollo; scripts auxiliares quedan en .tools, excluidos de Git.

### Archivos y límites

Backend: app/models.py, app/schemas.py, app/practice.py, app/main.py y
tests/test_practice.py. Frontend: src/api.js, src/views/TestView.vue,
src/views/PracticeView.vue, tests/api.test.js y tests/practice.test.js.
Se actualiza este registro y se reconstruye la aplicación local en puerto 5173.
No se cambia el seed, la generación de preguntas, dependencias ni README.

Se acepta perder un cambio si se cierra antes de terminar su petición. No hay
indicador de durabilidad antes de la confirmación HTTP ni bloqueo al salir.
Si distintas pestañas modifican la misma pregunta, gana la última escritura
procesada por el servidor; no se introduce edición colaborativa ni versionado.
La sesión sigue en memoria: recargar requiere login, aunque el borrador permanezca
en MongoDB. Continúan el aviso conocido de Starlette/TestClient y la validación
remota pendiente de CI/Sonar. No se hicieron operaciones de staging, historial,
ramas o configuración Git; se mantuvo develop y el trabajo previo sin versionar.

## 2026-11-04 — Corrección P2: conservar blurs en cola si falla la entrega

Se corrige únicamente la pérdida silenciosa identificada en la mini auditoría.
TestView sustituye el descarte de trabajos por `sending=true` por una promesa
que se resuelve al terminar el intento de entrega. La cola de cada pregunta
espera ese resultado: si el test sigue pendiente, continúa en su orden original;
si se completó, omite los trabajos restantes. La entrega sigue usando el formulario
actual y nunca espera las peticiones de borrador.

Los fallos de guardados que estaban en vuelo también esperan el resultado de
entrega antes de decidir si muestran el aviso existente. Así, si ambas operaciones
fallan, el aviso no desaparece silenciosamente; si la entrega termina bien, no
aparece un aviso de borrador sobre un test ya completado. Se mantienen el origen
exclusivo en blur, la comparación con el último valor confirmado y la secuencia
por pregunta. No se añaden temporizadores, reintentos automáticos ni guardados
al navegar. Backend, modelo de datos y protecciones de completados no cambian.

Se añaden cuatro regresiones: entrega fallida con varias versiones en cola
(la última queda confirmada), entrega exitosa sin nuevos PATCH de la cola y
dos combinaciones de fallo de autoguardado, en vuelo o en cola, con aviso y
formulario editable. Las 66 pruebas frontend pasan; ESLint, Prettier, build Vite
y git diff --check son correctos. Cobertura de líneas frontend: 97,78 %;
de ramas: 93,03 %. No se repite la suite backend porque no se modifica su código;
sus 98 pruebas aprobadas corresponden a la ejecución del incremento anterior.

Archivos de esta corrección: frontend/src/views/TestView.vue,
frontend/tests/practice.test.js y este registro. No se modifica README,
dependencias, configuración remota ni Git. CI/Sonar remotos siguen pendientes.

## 2026-11-05 — Incremento 3, fase 1: contratos, ficha educativa y prompt de banco

### Objetivo y límites

Preparar contratos para preguntas compartidas, metadatos de banco, progreso,
trazabilidad y métricas de generación sin conectar esos contratos al motor del
segundo incremento. No se crean colecciones nuevas, migraciones, índices,
reservas, estados preparados, tests mixtos, servicios worker ni llamadas LLM.
No se cambia Docker, dependencias, endpoints, registro, perfil o vistas Vue.

### Reutilización de Prompts

Se conserva PromptDocument, la colección Prompts y su índice compuesto de
asignatura/nombre/tipo/versión. El seed mantiene los dos borradores inactivos y
añade generacion_banco, tipo generacion, versión 1, como única plantilla
operativa en una instalación limpia. Su texto completo está en
backend/seed_data/prompts/generacion_banco.v1.txt, exclusivamente como recurso
de instalación del seed. No es un segundo almacén consultado por la lógica
de generación: app/prompts.py recupera el documento de MongoDB y no dispone
de una plantilla alternativa ni fallback al archivo.

Se usa $setOnInsert: las ediciones y desactivaciones previas se conservan.
Si ya hay otra plantilla activa de generación, la nueva se inserta inactiva;
no se desactiva automáticamente una elección previa. La lectura exige una
única plantilla de generación activa por asignatura y que corresponda a la
familia esperada. Ausencia, ambigüedad, familia distinta o variables inválidas
producen error; no se elige arbitrariamente la versión de mayor número.

La activación no llama al LLM y ninguna ruta actual consume el nuevo prompt.
No se ejecutó el seed contra la base de desarrollo: se probó exclusivamente
en MongoDB temporal. El documento se incorporará al ejecutar posteriormente
el seed actualizado en el entorno que corresponda.

El prompt exige una cantidad exacta de preguntas base, un hueco ___, respuesta
principal incluida en las aceptadas, dos distractores incorrectos, diversidad,
adecuación pedagógica y contenido apropiado para menores. Define JSON estricto,
límites de campos y prohibición de inventar IDs, secuencias, propietarios,
validación, métricas u otros metadatos que asignará el backend. El ejemplo JSON
incluido también se valida contra el contrato real en las pruebas.

La composición utiliza cuatro variables declaradas: tema, nivel_mcer, cantidad
y ficha_educativa. Se rechazan atributos, índices, conversiones y formatos en
placeholders. Solo se interpola una vez, sin reinterpretar llaves contenidas
en la ficha. La asignatura de consulta se valida como ObjectId.

### Contenido y contratos

Se añade la ficha content/ingles/present-simple/A1/v1.md dentro del backend:
afirmativa de verbos léxicos, hábitos/rutinas, concordancia, -s/-es/-ies y have/has;
objetivos, ejemplos, errores frecuentes, exclusiones y criterios de distractores.
A1 representa dificultad objetivo, no una equivalencia con 1.º ESO ni un nivel
diagnosticado del alumno. La selección de curso al registrarse se conserva.

app/content.py carga únicamente fichas de un registro explícito del servidor.
No acepta rutas proporcionadas por clientes ni sustituye niveles/versiones
desconocidos por otros. Incluye referencia de versión y SHA-256, validado contra
el texto. read_text normaliza finales de línea para obtener el mismo hash en
Windows y Linux. Las fichas están separadas de la plantilla de generación.

app/bank_contracts.py reutiliza Model/Document, ObjectId BSON, MCER, fechas UTC,
BlankSolution, Question y AIModel. Define:

- Banco y edición, ficha correspondiente y último registro de generación.
- Asignación curricular explícita de asignatura/curso/tema a banco, sin activarla.
- Pregunta base individual, respuesta principal, alternativas y distractores.
- Salida LLM acotada que rechaza campos adicionales y enunciados repetidos.
- Validación estructural separada de revisión pedagógica. Marcar una pregunta
  apta para opción múltiple exige dos distractores y revisión pedagógica.
- Procedencia manual/LLM; el origen LLM requiere modelo, prompt/version,
  referencia de ficha y ID de generación asignado por el backend.
- Cursor independiente por usuario/banco, con versión y contador no negativo.
- Snapshot privado con ID original, banco, secuencia y versión de pregunta.
  Se admiten contratos de ambas modalidades, sin activar su creación o corrección.
- Métricas por intento: modelo, prompt/version, ficha, latencia, tokens de entrada
  y salida, coste estimado USD, preguntas solicitadas/recibidas/válidas y errores
  estructurados. Datos desconocidos son null; no se presentan como coste cero.

Los contadores son enteros estrictos; se rechazan costes negativos/no finitos.
Un intento puede registrar más preguntas recibidas que solicitadas, ya que esa
respuesta incorrecta también debe poder medirse; válidas no supera recibidas.
Los metadatos se serializan a BSON y las respuestas generadas no los controlan.
No se calcula ni estima automáticamente el coste sin proveedor/tarifas definidos.

### Pruebas y comprobaciones locales

| Comprobación | Resultado |
| --- | --- |
| Suite backend completa con MongoDB aislado | 163 aprobadas: 98 previas + 65 nuevas |
| Nuevas pruebas de contratos | 35 casos |
| Nuevas pruebas de ficha/plantilla | 25 unitarias + 5 de integración |
| Cobertura backend de líneas, sin ramas | 848/876 = 96,80 %, mostrado como 97 % |
| Nuevos módulos bank_contracts/content/prompts | 100 % de líneas, no prueba de corrección pedagógica |
| Ruff / formato | Correctos, 19 archivos Python |

Se comprueban preguntas malformadas, límites, normalización de alternativas,
distractores repetidos o aceptados, metadatos LLM prohibidos, procedencia,
snapshots de ambas modalidades, cursores, métricas, hashes, rutas, composición
y cantidad de salida. MongoDB real prueba lectura del texto editado en BD,
ausencia de fallback, conservación de ediciones/desactivaciones, conflictos de
activación y seed concurrente de la misma versión. La suite previa conserva
las comprobaciones de autorización, privacidad, autoguardado y corrección.

Se utilizó el proyecto temporal trainer-phase1-tests y el override local de CA,
sin cambios en archivos Docker ni datos reales. No se repiten tests/lint/build
frontend porque no se modificó frontend. Sigue el aviso conocido de
Starlette/TestClient sobre httpx. CI/Sonar de estos cambios requieren una nueva
ejecución remota; no se ha publicado ni cambiado configuración externa.

### Riesgos y decisiones para la fase 2

Los contratos no validan relaciones contra MongoDB ni crean índices; esas
garantías requieren servicios, migración y pruebas de integración posteriores.
No existe aún asignación operativa curso/tema/banco ni equivalencia MCER global.
La validación estructural no demuestra corrección gramatical o de distractores:
la política de revisión y publicación continúa pendiente.

Sin modificar índices ni introducir transacciones, no se garantiza una única
activación frente a escrituras administrativas o seeds de versiones diferentes
que compitan. Se conserva la seguridad de lectura: cualquier ambigüedad bloquea
su utilización. El seed concurrente de la misma versión sí utiliza el índice
compuesto existente. La estrategia futura de activación debe decidirse antes
de habilitar generación real. También quedan pendientes proveedor, presupuestos,
leases, persistencia histórica de métricas y replica set/reservas atómicas.

### Archivos e impacto en la memoria

Nuevos: app/bank_contracts.py, app/content.py, app/prompts.py, ficha Markdown,
recurso del seed y tests/test_bank_contracts.py y tests/test_prompts.py.
Modificados: app/seed.py, tests/test_models.py, tests/test_auth.py y este registro.
Los tests existentes solo ajustan la expectativa de dos a tres prompts.
No se modifica README ni se realizan operaciones Git de escritura. La edición
previa de .gitignore se conserva sin cambios. Esta fase permite explicar la
separación contenido/plantilla, contratos independientes del proveedor,
trazabilidad reproducible y evolución compatible del motor determinista.

## 2026-11-14 — Fase 1: idioma objetivo en generacion_banco v2

Se añade idioma_objetivo al contrato interno GenerationParameters con valor
predeterminado y único admitido Inglés, reutilizando la etiqueta de la asignatura
del seed. El campo queda disponible para una resolución futura desde asignatura
o banco. Los contratos públicos y las vistas conservan su comportamiento.

La nueva plantilla generacion_banco.v2.txt utiliza la variable para enunciados,
respuesta principal, alternativas y distractores. Conserva las restricciones y
el esquema JSON de v1. La plantilla v1 y sus metadatos originales se mantienen.
El renderizador valida las cuatro variables de v1 y las cinco de v2 según la
versión; rechaza combinaciones incompatibles y versiones todavía no soportadas.

El seed prepara v2 por defecto con ID y fecha propios. $setOnInsert conserva
texto, variables y estado de cualquier documento existente. Si v1 está activa,
v2 se inserta inactiva; si v1 está inactiva y no hay otra generación activa,
v2 puede insertarse activa. Una instalación limpia recibe v2 y los dos borradores.

Para activar v2 en una base con v1 activa, validar primero el documento v2 y
realizar mantenimiento con lectores/escritores de generación detenidos.
Desactivar las plantillas de generación activas de la asignatura, activar
únicamente generacion_banco versión 2 y comprobar que el total activo sea uno
y que load_generation_prompt recupere v2. Durante el intervalo sin plantilla
activa, el cargador rechaza la lectura. Repetir el seed mantiene la elección.
Este procedimiento requiere exclusión de otros administradores; la unicidad
de activación sigue siendo una validación de lectura, sin un nuevo índice.

Validación local: 172 pruebas backend aprobadas, nueve adicionales a las 163
anteriores. Incluyen idioma inglés, sustitución del marcador, v1/v2, variables
por versión, exclusión del idioma de CreateTestRequest, seed repetido con v1
activa/inactiva y activación controlada de v2. Cobertura de líneas sin ramas
857/885 = 96,84 %. Ruff y formato correctos en 19 archivos. Continúa el aviso
conocido de Starlette/TestClient. MongoDB fue efímero en trainer-language-tests;
no se ejecutó el seed ni la activación en desarrollo y no hubo llamadas LLM.

Archivos del ajuste: app/prompts.py, app/seed.py, recurso generacion_banco.v2.txt,
tests/test_prompts.py, tests/test_models.py y los dos documentos de seguimiento.
La fase 2 continúa pendiente. El ajuste documenta la separación entre idioma
objetivo resuelto por backend y variables versionadas de la plantilla.

## 2026-11-16 — Fase 2B: persistencia nueva y ensayo de restauración

### Alcance autorizado y decisiones

El desarrollador confirma que la base anterior solo contiene datos de prueba
y autoriza omitir su migración. Se implementa en bases aisladas un banco por
tema curricular y dificultad MCER, sin ediciones del banco. ADR-009 y ADR-010
registran el alcance aplicado y sustituyen para el MVP las propuestas ADR-002
y ADR-008, conservando su historial. Replica set y reserva secuencial siguen
pendientes; se mantiene la topología standalone existente.

### Modelo y flujo implementados

Bancos se añade a las cinco colecciones anteriores. Su clave única es
(tema_id, nivel_mcer_objetivo). El contrato conserva código de contenido,
referencia de ficha/version/hash, publicado_hasta y última métrica opcional.
BancoEjercicios contiene diez documentos individuales del seed, con banco_id,
secuencia única por banco, versión de pregunta, enunciado, principal, solución,
procedencia manual y validación. Las preguntas iniciales no reciben distractores
inventados; aún no se presentan como opción múltiple.

El tema Present Simple declara A1 como dificultad objetivo explícita de ese
contenido. El backend valida el tema contra asignatura y curso del perfil y
selecciona exclusivamente su banco MCER configurado. No se introduce una
equivalencia global curso/MCER ni se evalúa el nivel individual del alumno.
La práctica usa las primeras hasta diez preguntas revisadas y publicadas,
ordenadas por secuencia. No hay consumo ni cursor operativo; puede haber
repetición entre intentos, como antes de la fase 3.

Cada intento copia enunciados y soluciones con ID global de pregunta; su
procedencia conserva banco, ID original, secuencia y versión. QuestionOrigin
anterior y BankQuestionOrigin nuevo conviven en el lector de Tests. Se prueba
recuperar un snapshot anterior con borrador y completarlo tras retirar el banco.
Se conservan autorización por propietario, privacidad y transiciones atómicas
de pendientes, entregas y descartes. La API mantiene el flujo actual de Vue.

El seed inserta preguntas con IDs estables y $setOnInsert antes de publicar el
banco. Repetirlo conserva documentos editados e índices y permite recuperar una
instalación inicial parcial. Una base nueva persiste generacion_banco v2 activa,
las cinco variables y el texto completo; el test consulta el documento real.
Se mantiene la conservación de v1 activa si ya existe. Una base con el array
antiguo o su índice tema_unique se rechaza antes de escribir. No se ejecuta
un borrado, una migración implícita ni un drop de índices contra esa base.

### Copia y restauración ejecutadas

scripts/Test-BackupRestore.ps1 crea un proyecto Compose con nombre único, red y
tmpfs propios usando compose.test.yml. backend/scripts/backup_restore_check.py
prepara mediante FastAPI un usuario aleatorio de ensayo, un test completado y
otro pendiente con borrador. Los nombres de base y el entorno se validan como
exclusivos de prueba. No se consulta MONGODB_URI de desarrollo.

Se ejecutaron mongodump con archive/gzip y mongorestore con remapeo de namespace
a una segunda base vacía. Resultado real: 18 documentos restaurados de seis
colecciones y cero fallos. Antes del lifespan de destino se compararon todos
los documentos e índices con el origen, para no confundir restauración con
recreación posterior de índices por la aplicación. Después se validaron login,
perfil, consulta de ambos tests y entrega del pendiente en la base restaurada.
El origen se volvió a comparar y permaneció idéntico.

Los artefactos privados se conservan en .tools/backup-2bceb32df6fc40d590059907475e60b3,
fuera de Git, con archivo comprimido y estado de verificación. El SHA-256 se
calculó durante el ensayo. Los contenedores/red de ese ensayo se retiraron.
La base de desarrollo quedó intacta; no se arrancó el nuevo backend contra ella.
COPIA_RESTAURACION.md contiene el procedimiento, comandos, límites del dump
lógico y cómo ensayar futuras migraciones y rollback sobre clones.

### Comprobaciones locales efectivas

| Comprobación | Resultado |
| --- | --- |
| Backend completo, MongoDB aislado | 178 casos aprobados |
| Cobertura de backend/app, sin ramas | 865/892 = 96,97 % |
| Ruff y formato Python | Correctos, 21 archivos, incluido el verificador de restauración |
| Frontend | 66 casos aprobados |
| Cobertura frontend | 97,78 % de líneas, 93,03 % de ramas |
| ESLint, Prettier y build Vite | Correctos |
| Backup y restauración reales | Documentos e índices idénticos; flujo FastAPI operativo |

Se adaptaron pruebas del antiguo array al formato individual. Se conserva el
test del modelo legacy para compatibilidad de lectura; la antigua migración
automática de uso_practica se retira al quedar fuera del alcance autorizado.
La suite incluye índices únicos, seed nuevo/repetido/parcial, dificultad exacta,
rechazo sin escrituras de una base vieja con datos o solo con índice antiguo,
privacidad, respuestas y concurrencia. Los siete casos de test_persistence.py
complementan las suites previas. El script de backup se ejecuta como ensayo
separado y no entra en el porcentaje de backend/app.

### Archivos y límites

Backend: bank_contracts.py, models.py, database.py, seed.py y practice.py.
Pruebas: test_models.py, test_auth.py, test_bank_contracts.py, test_practice.py
y nuevo test_persistence.py. Nuevos scripts de ensayo Python y PowerShell.
Documentación: este registro, seguimiento, ADR y COPIA_RESTAURACION.md, con su
excepción en .gitignore. Se mantiene el README y las configuraciones Docker/CI.

La adopción en desarrollo requiere posteriormente una base/instancia nueva con
sus permisos, o una migración aprobada, antes de arrancar el código nuevo. Cambiar
solo MONGODB_DB sobre un volumen existente no provisiona permisos automáticamente.
Los servicios actuales solo crean índices y colecciones al arrancar; el contenido
inicial se carga mediante el comando explícito de seed descrito en el procedimiento.
Continúa el aviso conocido de Starlette/TestClient. No se ha ejecutado CI/Sonar
en remoto ni se han realizado operaciones Git de escritura.

## 2026-11-17 — Fase 3: reserva secuencial y cursor transaccional

### Objetivo y alcance

Reservar hasta diez preguntas publicadas por alumno y banco sin perder preguntas
si falla la creación del intento. Se conservan los contratos públicos, snapshots,
propiedad, borradores y corrector del incremento anterior. No hay preparados,
LLM, reposición automática ni cambios en el contenido inicial de diez preguntas.

### Implementación y decisiones

- Nueva colección Progreso con el contrato BankProgressDocument ya disponible.
  Índice único usuario_id/banco_id; ultima_secuencia_reservada es asignación,
  no número de respuestas correctas ni de preguntas entregadas. version sigue
  siendo la versión contractual, no un contador usado como lock.
- reservations.py concentra la creación del snapshot y la reserva. practice.py
  conserva validación académica y el resto del ciclo. No se acepta usuario_id,
  banco_id, cursor, puntuación o estado desde el JSON público de creación.
- El contador cero se provisiona con $setOnInsert antes de la transacción para
  evitar competir por un upsert dentro de ella. Su existencia no consume preguntas.
  El avance y la inserción del intento se confirman juntos en with_transaction.
- Se lee la publicación y cursor dentro de la misma sesión y se selecciona el
  intervalo [cursor+1, min(cursor+10, publicado_hasta)]. El bloque final puede
  tener menos de diez preguntas, conforme al nuevo alcance aprobado. Un hueco
  o pregunta publicada sin revisar devuelve 503 sin saltarla ni avanzar.
- Retomar, guardar borrador y entregar no cambian Progreso. Descartar elimina
  únicamente el pendiente propio; su cursor permanece consumido, incluso al
  reiniciar el backend. El índice parcial de un pendiente por alumno/tema sigue
  siendo la restricción global, además del cursor independiente por banco.
- Agotamiento: POST /tests devuelve 409 y «Has agotado las preguntas publicadas
  de este banco», conservando el progreso. Se ajusta PracticeView.vue porque
  su comprobación posterior de pendientes limpiaba ese mensaje. Se conserva el
  conflicto de otra pestaña que sí encuentra un pendiente que continuar.

### Consistencia y compatibilidad con 2B

ADR-003 y ADR-007 pasan a implementados localmente para reserva. Se selecciona
replica set de un nodo y transacción corta con snapshot/majority/primario, usando
el PyMongo asíncrono 4.18.2 existente. El callback no tiene llamadas externas.
Se permite que el driver reintente conflictos transitorios y commit de resultado
incierto. Presupuesto CSOT de diez segundos para la fase transaccional y cinco
segundos de max_commit_time_ms. No se implementa compensación del cursor.

Al arrancar, initialize_progress agrega los orígenes de los snapshots 2B por
usuario/banco e inserta solamente claves ausentes con la mayor secuencia. Se hace
antes del saneamiento de pendientes antiguos y de aceptar tráfico. Los intentos
conservan sus documentos completos. Varios intentos 2B que repetían 1–10 inicializan
10, no suman diez por intento. Un pendiente 2B sigue pudiendo retomarse o entregarse.
Los cursores existentes no se resetean al arrancar o repetir el seed.

Límites: los descartes físicos anteriores a fase 3 no se reconstruyen; sin
snapshots identificables se comienza en cero. Las referencias legacy sin secuencia
siguen legibles, pero no permiten inferir el progreso. Revisar la frontera si se
editó contenido 2B con huecos: el máximo considera consumido el prefijo anterior.
La adopción requiere detener escritores antiguos; no se hace en esta tarea.

### Entorno aislado y operación

compose.test.yml usa ahora MongoDB 8.0.15 como replica set autenticado, red
propia, tmpfs y sin puertos publicados. El arranque genera una clave interna
aleatoria fuera del repositorio; el healthcheck autenticado inicia solo un set
no configurado y espera primario. .gitattributes fija LF para el script Bash
montado desde Windows. No se añaden dependencias ni se modifica docker-compose.yml.

compose.replica.yml permite una adopción manual sobre una copia del entorno.
La sintaxis de ambos Compose se validó. El volumen de desarrollo no se abrió,
convirtió ni arrancó. Un standalone no permite nuevas reservas y devuelve 503;
no se incorpora un camino alternativo de dos escrituras no atómicas. La guía
COPIA_RESTAURACION.md explica instalación vacía, restauración de una base 2B en
otro proyecto y precauciones para inicializar los cursores. El overlay en la
instalación de desarrollo continúa pendiente de validación manual.

### Pruebas efectivamente ejecutadas

| Comprobación | Resultado local |
| --- | --- |
| Backend completo, MongoDB replica set aislado | 195 aprobadas, 36,83 s en la ejecución final |
| Cobertura backend/app, sin ramas | 906/938 = 96,59 % de líneas |
| Ruff y formato | Correctos, 23 archivos Python |
| Frontend completo | 67 aprobadas |
| Cobertura frontend | 97,79 % líneas; 93,07 % ramas; 96,19 % sentencias; 98,94 % funciones |
| ESLint, Prettier y build Vite | Correctos |
| Compose de pruebas y overlay replica | Sintaxis válida |
| Backup/restauración real en replica set | 29 documentos, siete colecciones, cero fallos |

Se añaden 17 casos en test_reservations.py y se adaptan pruebas anteriores que
asumían reutilizar las primeras preguntas. El banco sintético de pruebas tiene
20 preguntas, con variantes de 23 y 40 para bloques parciales y regresión.
Se verifican 1–10/11–20, independencia de alumnos/bancos, índice único de Progreso,
reanudación, borrador, entrega, descarte, agotamiento y contenido publicado inválido.
Se prueban reinicios con pendientes/completados 2B y reserva posterior desde 11.

Hay fallos controlados antes del cursor, tras modificarlo y después de insertar
el snapshot, tanto en el primer bloque como tras otro completado: la transacción
real se aborta, conserva el cursor anterior y permite reintentar el mismo bloque.
Otro caso inyecta TransientTransactionError después de insertar; otro pierde la
respuesta de un commit real e inyecta UnknownTransactionCommitResult. El driver
reintenta sin duplicar el intento ni consumir dos bloques. Son inyecciones en el
driver, no un corte real de red ni pruebas de failover o caída del nodo.

Cuatro peticiones sincronizadas leen el mismo cursor; solo una crea el pendiente,
las otras reciben 409 y se comprueba que el driver repitió callbacks tras los
conflictos reales de escritura. Otra prueba usa dos instancias de FastAPI contra
la misma base. Las suites anteriores mantienen autorización y privacidad.
Frontend incorpora una regresión para mostrar el agotamiento sin cerrar sesión.

El script Test-BackupRestore.ps1 se repite con contenido sintético ampliado en
su base efímera. Se copian y restauran veinte preguntas, un usuario, dos intentos,
un catálogo, un banco, tres prompts y un Progreso. Antes del lifespan de destino
coinciden todos los documentos e índices, incluido usuario_banco_unique. Después
funcionan login, perfil, resultados, borrador y entrega. El origen sigue idéntico.
Artefactos privados en .tools/backup-cc5a0b02499d4f15b234a68b4210974f, excluidos de
Git; contenedores y red del ensayo retirados. Se conserva la evidencia histórica
de dieciocho documentos de 2B, que corresponde al esquema de aquel momento.

La primera regresión backend detectó cinco expectativas del formato/selección
anterior (colecciones y reutilización de bloques); se adaptaron antes de la suite
final. La nueva prueba Vue tuvo una aserción incorrecta sobre un token que no es
público en auth.state; se corrigió para comprobar la sesión autenticada. La
ejecución final de frontend pasa completa. Persiste la advertencia conocida de
Starlette/TestClient; no se cambian dependencias por ella.

### Archivos, seguimiento y límites pendientes

Backend: database.py, practice.py, nuevo reservations.py. Pruebas: conftest.py,
test_auth.py, test_practice.py, nuevo test_reservations.py. Infraestructura local:
compose.test.yml, nuevo compose.replica.yml y scripts mongo-replica de docker/;
LF específico en .gitattributes. Frontend: PracticeView.vue y practice.test.js.
El verificador Python de backup se adapta a dos bloques. Se actualizan ADR,
esta guía técnica, SEGUIMIENTO_CONTROL_TFG.md y COPIA_RESTAURACION.md.

El seed real sigue ofreciendo diez preguntas: tras reservarlas, incluso si se
descarta el intento, ese alumno agotará el banco hasta una publicación posterior.
La reposición automática, estados preparados, mixtos, LLM y coordinación de lotes
permanecen fuera del alcance. Un único nodo no proporciona alta disponibilidad.
No se ha validado CI/Sonar remoto ni realizado operaciones Git de escritura.
El README y el volumen de desarrollo se mantienen intactos.

Comprobación final: coverage.xml resuelve sus trece archivos de backend/app
desde la raíz del repositorio. Sintaxis Bash, JavaScript del healthcheck y
PowerShell correctas; git diff --check correcto. No se encontraron coincidencias
con los secretos locales en los diecinueve archivos revisados; los artefactos
privados siguen excluidos. Los ADR y SEGUIMIENTO se actualizaron localmente,
pero siguen ignorados por la configuración .gitignore actual del desarrollador,
que no se modifica en esta fase. No hay archivos en staging.

Reproducción de backend desde PowerShell, con Docker Desktop activo:

```powershell
docker compose -p trainer-reservations-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests --attach tests
docker compose -p trainer-reservations-tests -f compose.test.yml down --remove-orphans
```

En este equipo la construcción usó además `-f .tools/compose.test.ca.yml`, la
configuración local de CA ya existente y excluida. La limpieza retira únicamente
el proyecto de pruebas indicado. Los servicios de desarrollo no se detuvieron.

## 2026-11-17 — Reposición del banco en segundo plano con proveedor inyectable

### Objetivo y decisión de alcance

Continuar desde fase 3 solicitando un lote compartido cuando, tras confirmar una
reserva, quedan veinte o menos preguntas por delante del cursor. Se empieza por
lotes de treinta, con ambos parámetros configurables. El desarrollador indica
expresamente mantener proveedor/modelo pendientes en los ADR, usar la simulación
solo en pruebas y no anunciar una preparación ficticia en el entorno habitual.
No se conecta un servicio externo, no hay consumo de saldo y no se implementan
feedback ni tests preparados. La fase se registra como F4A, no como integración
LLM real terminada.

### Implementación y arquitectura

- generation.py define el puerto GenerationProvider y los contratos internos de
  petición/respuesta. Se inyecta al crear FastAPI. No hay proveedor de respaldo
  ni simulación en app; el único SimulatedProvider está en tests.
- replenishment.py implementa un worker asíncrono del lifespan con cola durable
  embebida en Bancos.reposicion, sin nuevas colecciones ni dependencias. El worker
  procesa un lote a la vez por instancia; distintas instancias se coordinan en
  MongoDB. La petición HTTP solo confirma la reserva y solicita la tarea.
- La condición de encolado compara publicado_hasta con la última secuencia del
  snapshot confirmado. Una actualización condicionada impide reemplazar una tarea
  pendiente/en curso/error por otra solicitud concurrente del mismo banco.
- El job conserva generacion_id, cantidad, intentos y estados pendiente/en_curso/
  error/completada. Un claim atómico asigna token y lease. Por defecto, timeout de
  proveedor de 60 s, lease de 90 s, tres intentos y cinco segundos entre reintentos.
  El lease se valida contra el timeout. El sondeo interno es de un segundo.
- La llamada al proveedor está fuera de las transacciones. Antes de publicar se
  verifica token/estado/vencimiento y referencia educativa; una transacción corta
  guarda nuevas preguntas y extremo publicado. Un worker tardío no puede publicar
  con un token sustituido. No se modifica Progreso ni Tests durante la reposición.
- Si falla el encolado tras el commit, el intento sigue devolviéndose correctamente.
  La solicitud sobre un banco agotado puede recuperar esa solicitud de reposición.
  No se afirma atomicidad entre commit de reserva y encolado posterior.

### Contenido y trazabilidad

Se reutilizan la plantilla activa versionada de Prompts, GenerationParameters y
la ficha cuyo hash/tema/MCER/version figuran en Bancos. El backend resuelve Inglés
como en fase 1. No se escribe una plantilla alternativa en negocio. En las pruebas
se comprueba v2 realmente recuperada y renderizada, no solo una cadena prefijada.

El lote completo pasa el contrato existente: cantidad exacta, un hueco, principal
entre aceptadas, dos distractores diferentes de las soluciones, ausencia de campos
internos y enunciados duplicados. Un lote inválido se rechaza íntegro. Se excluyen
enunciados ya existentes en el banco normalizando espacios y mayúsculas, y las
aceptadas se insertan con secuencias contiguas después de publicado_hasta. Si todas
son repetidas, la tarea falla sin publicar. No se deduplican equivalencias semánticas.

Cada pregunta nueva conserva origen llm y generación/modelo/prompt/ficha; la
versión inicial de pregunta es 1. Se introduce pedagogica=automatica para no
atribuir una revisión humana que no existe. Esta validación NO acredita corrección
gramatical ni calidad pedagógica. No se habilita apta_multiple_choice. La revisión
de aceptación educativa se mantiene pendiente al elegir proveedor y modelo.

ultima_generacion registra métricas del último intento con contexto válido, con
errores seguros. Latencia también para fallos; tokens/coste desconocidos son None.
Se corrigieron advertencias iniciales de serialización construyendo GenerationError
tipado al actualizar las métricas. No se guarda texto de excepciones del proveedor
ni se añade un histórico completo de intentos/costes.

### API, Vue y ausencia de proveedor

Los éxitos de POST /tests mantienen su DTO. Agotamiento devuelve 409 con detail y
codigo: banco_reponiendo, reposicion_fallida, proveedor_no_configurado, banco_agotado
o preguntas_disponibles si una publicación concurrente ya dejó contenido nuevo.
No se añaden parámetros públicos ni endpoints de administración de generaciones.
Autorización, borradores y corrección determinista continúan funcionando.

Sin proveedor no hay worker ni encolado, y una tarea antigua no provoca la respuesta
banco_reponiendo. Vue muestra preparación solo ante ese código. Distingue fallo y
falta de configuración, permite comprobar disponibilidad sin polling y ofrece otro
tema únicamente si existe en el catálogo del alumno. La navegación sigue usando
Vue Router y el token en memoria.

### Comprobaciones ejecutadas

| Comprobación | Resultado local |
| --- | --- |
| Backend completo con MongoDB replica set aislado | 221 aprobadas, 49,51 s en la ejecución final |
| Cobertura backend/app, sin ramas | 1110/1151 = 96,44 % de líneas |
| Ruff y formato | Correctos, 26 archivos Python |
| Frontend completo | 72 aprobadas |
| Cobertura frontend | 98,40 % líneas; 93,42 % ramas; 96,81 % sentencias; 98,96 % funciones |
| ESLint, Prettier, build Vite | Correctos |
| Informe XML | Quince archivos backend/app resueltos desde la raíz |
| Compose habitual más overlay replica | Configuración válida; no se arrancó desarrollo |

test_replenishment.py añade 26 casos: umbral por defecto y configurable; frontera
de veinte frente a veintiuna preguntas; lote configurable; petición que devuelve
el intento mientras el proveedor sigue bloqueado; dos alumnos/dos workers; fallo
y timeout; reintento con identidad estable; proveedor ausente y tarea antigua;
lotes inválidos y duplicadas; rollback tras insertar; worker con lease vencido;
recuperación tras reinicio; commit incierto; fallo al encolar; autorización y
contexto sin prompt activo. Se conserva toda la regresión anterior de fase 3.

Los fallos de publicación y respuesta de commit se inyectan en el driver sobre
escrituras/commit reales en MongoDB. No se simula una caída física o partición real
de red ni se prueba un proveedor comercial. La simulación y todas las preguntas
generadas en estas pruebas permanecen en bases aleatorias efímeras.

Frontend añade cinco casos para interpretar el código HTTP, estado preparando,
alternativas de tema, ausencia de proveedor y fallo. Pasan también las pruebas
previas de autenticación, confirmación de entrega y autosave exclusivamente blur.
La advertencia conocida de Starlette/TestClient persiste; no se cambian dependencias.

### Archivos y operación

Nuevos: backend/app/generation.py, replenishment.py y tests/test_replenishment.py.
Modificados: bank_contracts.py, config.py, main.py, reservations.py; frontend/src/api.js,
views/PracticeView.vue y pruebas api/practice. docker-compose.yml y .env.example
exponen solamente BANK_REFILL_THRESHOLD y BANK_REFILL_BATCH_SIZE (20/30).
Los controles internos del worker se configuran mediante Settings/entorno del
proceso. Se actualizan ADR-005/006/007, seguimiento y nota de recuperación.
Los ADR y seguimiento siguen siendo locales/ignorados por las reglas del autor.
No se cambia README, configuración Git ni requisitos/dependencias.

Para repetir la suite desde PowerShell con Docker Desktop:

```powershell
docker compose -p trainer-refill-tests -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests --attach tests
docker compose -p trainer-refill-tests -f compose.test.yml down --remove-orphans
```

En este equipo se añadió el override local .tools/compose.test.ca.yml para la CA
de construcción. Se retiraron únicamente contenedores y red de trainer-refill-tests.
El volumen y los servicios de desarrollo quedaron intactos. No se ejecutó CI/Sonar
remoto ni se realizaron operaciones Git de escritura.

### Límites y decisiones pendientes

Elegir proveedor/modelo y adaptador real; evaluar contenido, costes y latencias;
definir histórico completo de métricas y operación tras error terminal. Un lote
puede requerir otra llamada tras una caída anterior a la publicación: se garantiza
no duplicar publicaciones, no facturación externa exactamente una vez. El payload
pendiente no se persiste. La deduplicación recorre enunciados del banco al publicar;
se revisará una estrategia de huellas/índice cuando el volumen lo justifique.
Los tests preparados, mixtos y feedback siguen pendientes de sus fases.

## 2026-11-18 — OpenRouter/Anthropic BYOK y adopción de .env sin cambiar datos

### Objetivo y alcance

Integrar OpenRouter reutilizando GenerationProvider, restringirlo a Anthropic,
preparar una llamada manual de cinco preguntas Present Simple A1 sin publicación
y adoptar el entorno de fase 3 mediante .env normal. El desarrollador ejecutará
la llamada real, el arranque con datos y las operaciones Git. Solo se han probado
respuestas simuladas y bases de pruebas aisladas.

### Adaptador y validación

OpenRouterProvider usa Chat Completions en un endpoint HTTPS fijo. Se mantiene
HTTPX 0.28.1, ya existente en desarrollo, promovido a requirements de runtime;
no se instala SDK ni se cambia esa versión. El modelo es configurable por
OPENROUTER_MODEL, restringido a identificadores anthropic/. Haiku 4.5 se deja
como ejemplo inicial configurable, sin afirmar superioridad medida.

La solicitud incluye only=[anthropic], allow_fallbacks=false, require_parameters=true
y JSON Schema estricto. No hay redirects, reintentos HTTP, modelos alternativos
ni llamadas adicionales para reparar la salida. Se conserva el prompt completo
versionado y el contrato GenerationOutput. La preparación de ficha/plantilla
se comparte entre el worker y el comando manual mediante generation.py.

Se comprueban proveedor Anthropic, modelo y confirmación BYOK de la respuesta;
se rechaza una confirmación ausente, falsa o contradictoria. BYOK requiere además
bloquear capacidad compartida en el workspace; el filtro de proveedor no sustituye
ese ajuste. La comprobación posterior no revierte una factura ya producida.
No se accede a la cuenta ni se configura el workspace desde el backend.

GenerationResponse añade metadata opcional de llamada/modelo/proveedor/BYOK y
mantiene compatibilidad con los simulados anteriores. Los tokens se conservan si
se conocen; no se confunde usage.cost=0 con el coste total Anthropic BYOK.
Los errores omiten cuerpos HTTP y credenciales. La clave Anthropic permanece
en el workspace; solo OPENROUTER_API_KEY entra al entorno privado del backend.

### Ensayo manual y reposición desactivada

BANK_REFILL_ENABLED=false por defecto. Configurar una clave no arranca el worker.
Se conserva la inyección explícita de proveedores de prueba. Si falta clave, la
API mantiene proveedor_no_configurado; si existe pero la reposición está apagada,
devuelve reposicion_desactivada y Vue muestra ese modo sin preparación ficticia.

scripts.preview_openrouter lee el banco Present Simple A1, su ficha y la plantilla
activa, pide exactamente cinco preguntas por defecto y devuelve JSON para revisión.
Muestra modelo, tokens e id de llamada si existe, sin secretos. No arranca FastAPI
ni ejecuta inicialización, seed, tareas o escrituras MongoDB; no hay opción de
publicación. Rechaza ejecutarse con la reposición activada. Las pruebas verifican
una sola llamada y la igualdad de documentos/índices antes y después en base aislada.
La revisión lingüística de las preguntas sigue siendo necesaria.

### Entorno observado y procedimiento de adopción

Docker Desktop estaba detenido al comenzar y se inició para las comprobaciones
aisladas. Solo se inspeccionaron metadatos del entorno existente: proyecto
trainer-schema3, volumen trainer-schema3_mongodb_data, base trainer, réplica
declarada trainer-local y host mongodb:27017. Se comprobó la coincidencia de
credenciales y JWT con .env.schema3 sin imprimir valores. No se abrió MongoDB de
desarrollo ni se ejecutó el procedimiento de adopción.

docker-compose.yml incorpora ahora la réplica que antes añadía el overlay.
compose.replica.yml se conserva como compatibilidad. El arranque normal toma
.env y un volumen nombrado explícito; su ausencia de configuración bloquea el
arranque. La adopción lo declara externo para evitar crear silenciosamente otro
vacío. .env.example queda reservado para una instalación nueva y volumen propio.

Use-Phase3Environment.ps1 inspecciona contenedores, contrasta configuración y
prepara .env desde schema3 conservando proyecto, volumen, base, contraseñas y JWT.
Guarda el destino anterior en .tools, conserva una clave OpenRouter ya introducida
en el destino y mantiene la reposición apagada. No ejecuta comandos Docker de
escritura, seed, migraciones ni consultas MongoDB. No modifica schema2/schema3.
No se ha ejecutado contra el .env real; los tres archivos privados se conservan.

data_snapshot hace inventario de solo lectura con base, replica set, recuentos y
hashes de documentos e índices de todas las colecciones, sin imprimir contenido
privado. El procedimiento pausa escritores, arranca solo MongoDB en el mismo
volumen, toma una referencia antes de arrancar el backend nuevo y compara después.
No se deben crear/entregar tests ni editar borradores entre comparaciones.

### Comprobaciones efectivamente ejecutadas

| Comprobación | Resultado local |
| --- | --- |
| Backend completo, MongoDB replica set aislado | 252 aprobadas, 52,43 s en la ejecución final |
| Cobertura backend/app, sin ramas | 1186/1228 = 96,58 % de líneas |
| Ruff y formato | Correctos, 30 archivos Python |
| Frontend completo | 73 aprobadas |
| Cobertura frontend | 98,40 % líneas; 93,42 % ramas |
| ESLint, Prettier, build Vite | Correctos |
| Imagen backend de runtime | Construcción correcta con HTTPX instalado |
| CLI de preview en imagen sin red | --help correcto, sin llamar al proveedor |
| Adopción PowerShell | Simulada con credenciales y Docker ficticios; origen preservado y rechazo sin volumen |
| Compose | Configuración simulada preserva proyecto/volumen externo, réplica y flag apagado |

Se añaden 31 casos en test_openrouter.py: solicitud única, proveedor/modelo,
BYOK, parámetros, JSON Schema, errores HTTP/timeout/red/redirect sin reintentos,
salida truncada, rechazo del modelo, JSON y campos manipulados, cantidad y tokens,
metadata opcional y modelo configurable. La integración del preview comprueba
la plantilla realmente leída desde MongoDB y ausencia de publicaciones. Otra prueba
comprueba que una clave no activa el worker y que Vue recibe el estado desactivado.
La factoría habilitada explícitamente se prueba con transporte simulado.

Los errores iniciales de longitud de línea se resolvieron antes de los resultados
finales. Se corrigió también el lector PowerShell para conservar nombres de
variables al procesar valores entrecomillados; el ensayo incluye ese caso.
Solo persiste la advertencia conocida de Starlette/TestClient.
El XML de cobertura resuelve dieciséis módulos de backend/app; no incluye los
scripts de administración en su denominador.

### Archivos y documentación

Nuevos: app/openrouter.py; backend/scripts/preview_openrouter.py y data_snapshot.py;
tests/test_openrouter.py; scripts/Use-Phase3Environment.ps1 y Test-Phase3Environment.ps1.
Modificados: config.py, generation.py, main.py y preparación del worker; flag de
estado en PracticeView.vue y su prueba; requirements; Compose y .env.example.
Se amplía COPIA_RESTAURACION.md con comandos exactos. Se registran ADR-011,
seguimiento y este devlog. Los ADR y seguimiento siguen ignorados por las reglas
actuales del autor. No se modifica README ni configuración/historial Git.
Se conservan los cambios no confirmados de la reposición del día anterior.

### Pendientes y límites

El autor debe introducir su clave OpenRouter en .env, configurar BYOK sin
capacidad compartida, adoptar el entorno, comparar inventarios y verificar
login/intentos. Después ejecutará una única llamada real y revisará las preguntas.
La conectividad, soporte de parámetros del endpoint real, consumo y calidad
lingüística no quedan demostrados por simulación. No se han publicado preguntas
ni consumido saldo. Mantener BANK_REFILL_ENABLED=false durante esta revisión.
No se implementan feedback, tests preparados ni publicación del lote revisado.
No se han realizado operaciones Git de escritura ni validaciones remotas de CI.

## 2026-11-18 — CA de confianza en ejecución para OpenRouter

### Causa y ajuste acotado

La petición pública a /api/v1/models fallaba dentro del backend con
CERTIFICATE_VERIFY_FAILED. El Dockerfile solo montaba pip_ca_bundle durante el
RUN de pip: ese secreto de construcción no estaba disponible en ejecución.
Además, HTTPX con trust_env=False no adopta SSL_CERT_FILE automáticamente.

Se conserva el Dockerfile y se amplía el overlay local .tools/compose.ca.yml:
montaje de solo lectura del mismo bundle en /run/secrets/pip_ca_bundle y variable
SSL_CERT_FILE. openrouter.py pasa explícitamente ssl.create_default_context() a
HTTPX, conservando trust_env=False, CERT_REQUIRED y verificación del hostname.
No se incorpora la CA a la imagen ni se desactiva TLS. No hay dependencias nuevas.
El .env privado activa ambos archivos mediante
COMPOSE_FILE=docker-compose.yml;.tools/compose.ca.yml (separador Windows).
Se conserva una copia privada del .env anterior en .tools; no se cambian claves,
volumen, base ni BANK_REFILL_ENABLED=false.

### Comprobaciones efectivamente ejecutadas

- Reproducción inicial desde el backend: CA ausente en runtime y error de emisor
  local con urllib; sin autenticación ni generación.
- 30 pruebas unitarias del adaptador aprobadas; 3 de integración excluidas.
  Ejecución sin red. Dos regresiones nuevas verifican el contexto SSL seguro y
  su uso explícito por HTTPX con trust_env=False. Advertencia conocida de
  Starlette/TestClient; no se repiten las suites completas ni la cobertura global.
- Ruff y formato correctos en los tres archivos Python afectados.
- Reconstrucción y recreación únicamente del backend con --no-deps --build --wait.
- Nuevo diagnóstico scripts.check_openrouter_tls: urllib HTTP 200, HTTPX HTTP 200,
  478 modelos en ese momento, verificación TLS/hostname activas y sin Authorization.
  No se instancia el proveedor ni se utiliza la clave; no hay llamadas de generación.
- data_snapshot antes/después: misma base, replica set, documentos e índices de
  todas las colecciones. MongoDB conserva identificador de contenedor y volumen.
- Backend saludable, CA montada de solo lectura y BANK_REFILL_ENABLED=false.

### Reproducción y límites

Desde PowerShell, en la raíz: docker compose exec -T backend python -m
scripts.check_openrouter_tls. El comando solo consulta el catálogo público usando
el mismo contexto TLS que el adaptador; no demuestra todavía generación ni BYOK.
El overlay, bundle, copias e .env son archivos privados ignorados por Git.
No se modifica README ni se realizan operaciones Git de escritura.
## 2026-11-18 — Adaptador Anthropic directo para previsualización

### Objetivo y alcance

Tras comunicar el autor HTTP 402 en OpenRouter, añadir una alternativa directa
para el primer ensayo de cinco preguntas. No se atribuye la causa del 402 sin
más evidencia. Se conservan OpenRouter y la solución TLS; no se ejecutan llamadas
reales ni se publican preguntas. BANK_REFILL_ENABLED permanece false en .env y
en la configuración Compose comprobada. El .env local no se modifica en esta tarea.

### Implementación y decisiones

- app/anthropic.py implementa estructuralmente GenerationProvider usando HTTPX
  existente: endpoint fijo /v1/messages, x-api-key, versión 2023-06-01, modelo
  configurable (por defecto claude-haiku-4-5-20251001), salida JSON estructurada.
- Se reutiliza el contexto TLS de openrouter.py, con CA de ejecución, comprobación
  del certificado/hostname y trust_env=False. Sin redirects, reintentos ni fallback.
- El JSON Schema actual incluye longitudes y cardinalidades no admitidas por el
  servicio. Se eliminan solo esas restricciones del esquema enviado, trasladándolas
  a descripciones; la respuesta se valida siempre con el contrato original completo.
  No se modifica GenerationOutput ni la plantilla persistida.
- Settings y Compose incorporan ANTHROPIC_API_KEY (SecretStr), ANTHROPIC_MODEL y
  ANTHROPIC_MAX_TOKENS. .env.example solo contiene un campo de clave vacío.
- El comando existente preview_openrouter añade --proveedor anthropic; OpenRouter
  sigue siendo el valor predeterminado. Conserva la lectura del prompt/ficha/banco,
  el informe y la prohibición de escribir. No se modifica la selección automática
  del worker; Anthropic queda accesible únicamente por ensayo manual o inyección.
- Métricas: modelo, tokens y msg id (request-id como alternativa). Coste y bandera
  BYOK quedan desconocidos/null; API directa no implica BYOK de OpenRouter.
- Errores HTTP, conexión y validación usan mensajes controlados sin cuerpos o claves.

### Evidencia ejecutada

- Suite backend completa: 277 aprobadas en 54,34 s, MongoDB replica set exclusivo
  con tmpfs y red independientes; todas las respuestas de proveedores simuladas.
- 23 casos nuevos de Anthropic: selección CLI, cabeceras/modelo/TLS, petición única,
  errores HTTP/red sin reintentos, truncamiento/rechazo, esquema adaptado sin mutar,
  validación original (cantidad, metadatos, distractores, longitud, tokens), clave
  ausente y modelo configurable. La integración comprueba prompt v2 y hashes de
  documentos e índices antes/después idénticos, sin publicación.
- Cobertura backend/app: 1248/1292 = 96,59 % de líneas (no ramas); adaptador 56/58.
  Sin cubrir en el adaptador las respuestas sobredimensionadas y envoltorio inesperado.
- Ruff y formato correctos, 32 archivos Python. Ayuda CLI comprobada sin red.
- Imagen backend construida correctamente; Compose conserva volumen externo de
  fase 3, overlay TLS y reposición apagada. No se recrea el backend de desarrollo
  ni se escribe en su base; la adopción con la nueva clave la realizará el autor.
- Advertencia conocida de Starlette/TestClient; no se ejecutan pruebas frontend
  porque no hay cambios Vue. No se han validado CI remoto ni el servicio real.

### Documentación y revisión manual

Se amplía COPIA_RESTAURACION.md con las variables locales y el comando exacto;
se actualizan seguimiento y la decisión existente ADR-011, sin crear otro ADR.
El autor introduce su clave en .env, recrea solo backend y ejecuta una sola
previsualización de cinco preguntas antes de decidir cualquier publicación.
README y operaciones Git de escritura excluidos; cambios anteriores conservados.
## 2026-11-18 — Duplicados coherentes entre preview y reposición

### Diagnóstico

La reposición ya excluía preguntas existentes por enunciado normalizado dentro
del banco antes de publicar. El preview solo validaba el contrato del lote y
mostraba todas las preguntas como revisables; no consultaba duplicados del banco.
Por tanto, cambiar distractores no debía permitir publicación automática, pero
sí hacía que el ensayo manual mostrase duplicadas como si fueran candidatas.

El prompt v2 no recibe ejemplos consultados de BancoEjercicios ni una lista de
exclusión. prepare_generation_context solo incorpora parámetros y ficha educativa.
La ficha contiene ejemplos revisados, tres coincidentes literalmente con el seed.
La plantilla los presenta como contexto, exige diversidad y prohíbe copiar su
propio ejemplo de formato JSON; no separa dinámicamente referencias y enunciados
existentes a evitar. Este límite explica la posibilidad de repetir material.
No se modifica el texto v2 persistido, el seed ni el contrato/versionado de prompts.

### Ajuste implementado

- generation.py concentra sentence_key y filter_bank_duplicates, solo lectura.
  Normalización: split/join de espacios (incluye exteriores, repetidos y saltos)
  y casefold. Identidad por enunciado en el mismo banco; no por distractores,
  respuestas o metadatos. No se eliminan puntuación, palabras ni sinónimos.
- preview_openrouter usa el filtro después de validar el lote. Informa
  preguntas_recibidas, preguntas_descartadas_duplicadas y preguntas_candidatas;
  preguntas contiene solo las candidatas. Puede devolver cero sin error ni una
  segunda llamada. El selector de proveedor y las métricas se conservan.
- replenishment usa exactamente el mismo filtro dentro de su sesión/transacción
  de publicación, manteniendo lease, fencing, secuencia y manejo de lote sin
  preguntas nuevas. El snapshot del preview no sustituye esta comprobación final.
- No se agregan índices, campos persistidos, dependencias ni llamadas externas.
  BANK_REFILL_ENABLED=false verificado en Compose y en el backend en ejecución.

### Validación efectiva

Suite completa backend con MongoDB replica set aislado: 283 aprobadas en 54,95 s.
Cobertura de líneas backend/app: 1250/1294 = 96,60 %; no cobertura de ramas.
Ruff y formato: correctos en 33 archivos Python. Advertencia conocida de
Starlette/TestClient. Sin cambios frontend ni ejecución de sus pruebas.

Seis casos nuevos: normalización de mayúsculas/espacios/tabulaciones/saltos/
espacio no separable; preview de cinco preguntas con 5, 2 y 0 duplicadas.
El caso 5 usa enunciados existentes con dos distractores distintos y produce
5 recibidas, 5 descartadas, 0 candidatas y lista vacía. Cada ensayo hace una
petición simulada y compara inventario completo de documentos/índices antes y
después, sin cambios. Los casos parciales comprueban que la presencia en otro
banco no descarta una candidata del banco actual. Las regresiones existentes
del worker confirman descarte total y parcial, secuencias y atomicidad.

Se retira únicamente el proyecto efímero trainer-duplicates-tests y su red;
no se modifica la base ni volumen de desarrollo. No hay llamadas reales a ningún
proveedor, publicaciones, modificaciones de .env ni operaciones Git de escritura.

### Archivos y límites

Modificados generation.py, replenishment.py y preview_openrouter.py; añadido
backend/tests/test_preview_duplicates.py. Actualizados COPIA_RESTAURACION.md,
este devlog y seguimiento. README y prompts intactos.
El filtro detecta igualdad textual normalizada, no paráfrasis ni equivalencia
semántica. Proporcionar una lista de exclusión al LLM requerirá una evolución
explícita/versionada del contexto; no se incluye silenciosamente en este ajuste.
## 2026-11-18 — Prompt v3 y lotes completos con rondas acotadas

### Diagnóstico verificable

El autor comunica dos ensayos reales con cinco preguntas, todas ya existentes.
La aplicación de preview no conserva el prompt exacto de esas llamadas; no se
atribuye a reconstrucciones la condición de registro histórico. Se leyó de forma
exclusivamente consultiva el banco de desarrollo Present Simple A1 (diez preguntas),
la plantilla v2 persistida y la ficha/configuración actual. Se reconstruyó un prompt
para cantidad cinco y modelo claude-haiku-4-5-20251001, guardado localmente en
.tools/prompt-v2-reconstruido.txt (ignorado, sin secretos). Esto no prueba identidad
con las solicitudes originales ni conserva sus parámetros internos del proveedor.

Tres enunciados del banco aparecen literalmente en la ficha recibida por v2:
I ___ (play) football every Saturday.; She ___ (watch) TV after dinner every day.;
He ___ (go) to school by bus every morning. Los otros siete no aparecen allí.
V2 no consulta el banco para preparar exclusiones; sus reglas de variedad y ejemplos
no garantizan novedad. No se han realizado llamadas nuevas para este diagnóstico.

### Implementación y decisiones

- generacion_banco.v3.txt mantiene restricciones pedagógicas/salida de v2, añadiendo
  secciones explícitas ejemplos_pedagogicos y enunciados_excluidos. La exclusión es
  por enunciado, no por soluciones o distractores; sus datos no son instrucciones.
- prompts.py compone v3 extrayendo ejemplos revisados de la ficha y eliminando
  líneas coincidentes con exclusiones del contexto pedagógico. Los diez enunciados
  actuales del banco se incluyen en la lista; se añaden candidatas de rondas previas.
  La ficha original, hash y versiones 1/2 se conservan. No hay plantilla alternativa
  en lógica de negocio: el texto sigue leyéndose de Prompts.
- Seed idempotente prepara v3 inactiva; v2 sigue siendo la versión activa por defecto.
  scripts/activate_generation_prompt.py usa el mismo origen del seed y una transacción
  para insertar la versión ausente y cambiar a una única activa. Puede volver a v2.
  Solo se ejecuta contra bases aisladas en las pruebas; desarrollo no se activa.
- collect_generation_batch comparte preparación, filtrado y acumulación entre preview
  y worker. Pide solo pendientes, conserva candidatas durante las rondas y suma tokens.
  Una respuesta que incumple el contrato sigue rechazándose; validación estructural
  no equivale a revisión lingüística. La corrección determinista no se modifica.
- BANK_GENERATION_MAX_ROUNDS: predeterminado tres, rango uno a cinco. En el worker,
  rondas_consumidas se incrementa atómicamente antes de la llamada bajo el token/lease
  vigente. El contador durable incluye llamadas fallidas y no se reinicia al reintentar
  el mismo trabajo; documentos anteriores sin campo se interpretan como cero.
- El proveedor permanece fuera de transacciones. El timeout existente limita el conjunto
  de rondas de cada intento de ejecución, manteniendo la validez del lease.
- La publicación vuelve a consultar duplicados y exige exactamente la cantidad del
  trabajo. Una colisión tardía o agotamiento devuelve error sin insertar parcialmente
  preguntas ni avanzar publicado_hasta/progreso. Se registra la cantidad de candidatas
  válidas logradas en ultima_generacion; no se confunde con preguntas publicadas.
- Preview mantiene diagnóstico de una llamada y añade --modo lote. Informa candidatas
  y duplicadas de primera ronda, recibidas/duplicadas totales, candidatas finales,
  número de llamadas, detalle por ronda y tokens acumulados. Si es incompleto devuelve
  lote_incompleto_sin_publicar, junto con las candidatas; nunca publica ni rellena
  silenciosamente por encima del límite.

### Pruebas realmente ejecutadas

- Ejecución final: 293 pruebas backend aprobadas en 59,47 s, MongoDB replica set
  exclusivo con tmpfs y red independiente. Diez nuevos casos en test_generation_batches.
- Simulados: exclusión de los diez enunciados y separación pedagógica v3; activación
  y seed repetidos conservando v2/v3; 30 → 28 + 2; 5 → 0 + 5; diagnóstico de una llamada;
  agotamiento con cuatro candidatas y cero publicación; worker publica cinco o ninguna;
  contador durable entre fallos del proveedor; duplicado concurrente antes de publicación.
- Se adaptan expectativas antiguas: cuatro prompts (v2 activa/v3 inactiva), versión
  desconocida ahora cuatro y publicación previa de 29 ahora exige completar hasta 30.
- La primera ejecución detectó una expectativa antigua de tres prompts en test_auth;
  se actualizó a cuatro y se repitió la suite completa, sin fallos finales.
- Cobertura backend/app: 1317/1364 = 96,55 % de líneas, no ramas. XML generado.
  Los scripts administrativos no forman parte de ese denominador.
- Ruff y formato correctos, 35 archivos Python; imagen backend construida y ayuda de
  ambos comandos comprobada sin red. Compose conserva false, CA y volumen de fase 3.
- Advertencia conocida de Starlette/TestClient. Sin cambios Vue; no se repite frontend.

### Límites y entrega

Las candidatas se conservan en memoria durante el lote, sin recuperación tras caída.
Un fallo del proveedor o contenido inválido aborta la ejecución conservadora; los
reintentos siguen sujetos al contador durable. La novedad es comparación normalizada
textual, no semántica. La lista de exclusión crecerá con el banco y puede aumentar
los tokens; el MVP usa su banco pequeño completo. No se consumen créditos en pruebas.
Se conservan los adaptadores, TLS, base y volumen de desarrollo, con
BANK_REFILL_ENABLED=false. Solo se consulta desarrollo para reconstruir v2.
Instrucciones manuales de activación/previsualización en COPIA_RESTAURACION.md.
Se actualizan seguimiento y ADR-011 existente; sin ADR nuevo, README ni Git de escritura.
## 2026-11-19 — Diagnóstico de errores Anthropic sin perder metadatos

### Problema observado y causa comprobada

El autor comunica éxito con cinco preguntas y fallo de contrato con treinta.
En AnthropicProvider.generate el except conjunto de KeyError/ValueError/etc.
incluía tanto JSON inválido como Pydantic, modelo y stop_reason; sustituía todos
los detalles por el mismo mensaje. La CLI solo imprimía str(error). No existe
información conservada que permita atribuir el fallo pasado a truncamiento,
cantidad o una regla concreta. No se ha repetido ninguna llamada real.

### Implementación

- AnthropicError conserva un diagnóstico estructurado y mantiene str(error)
  controlado. Se extraen metadatos antes de validar la respuesta: modelo, msg id
  (request-id como alternativa), stop_reason, tokens válidos y número de preguntas
  si el JSON pudo leerse. Campos desconocidos permanecen null.
- Se separan errores de sobre/tamaño, modelo, finalización, bloque textual, JSON,
  contrato Pydantic, cantidad y metadatos. Pydantic aporta ruta/tipo/mensaje con
  include_input=False, include_context=False e include_url=False. Las reglas de
  validadores de pregunta y lote se conservan, sin imprimir valores rechazados.
- GenerationRequest añade ronda interna con valor predeterminado uno; el colector
  la asigna a cada llamada. No cambia API pública, esquema de salida ni persistencia.
- La CLI muestra el diagnóstico y termina con código 1 cuando hay error. En lote,
  la ronda identifica la llamada fallida. Los tokens del error son los de esa
  llamada; los informes exitosos mantienen la suma ya existente.
- --guardar-texto habilita exclusivamente en CLI Anthropic un callback para escribir
  solo el texto devuelto en .tools, con nombres UUID y sin sobrescritura. Redacta
  cualquier aparición de la clave Anthropic configurada. No se guardan petición,
  sobre HTTP ni cabeceras. El adaptador/worker habituales no activan capturas.
- La captura ocurre antes de rechazar el contenido, también en max_tokens o JSON
  inválido. Un fallo de escritura genera aviso sin ocultar el fallo del contrato.
  La protección de tamaño y todas las validaciones, TLS y no publicación permanecen.

### Evidencia ejecutada

- Suite backend completa: 306 pruebas aprobadas en 66,74 s, con MongoDB replica set
  aislado y respuestas simuladas. Trece casos nuevos en test_anthropic.py.
- Casos: cantidad incorrecta; huecos/longitud; duplicación dentro del lote; JSON
  inválido; max_tokens con JSON válido o truncado; tokens de tipo inválido; texto
  guardado en éxito y error con redacción de clave y exclusión de cabeceras;
  fallo de escritura sin perder validación; pregunta 18 inválida en respuesta de
  treinta; CLI fallida en segunda ronda con comparación completa de documentos
  e índices antes/después, sin cambios.
- Cobertura backend/app: 1370/1422 = 96,34 % de líneas, sin ramas. XML generado.
  Scripts administrativos excluidos del denominador como en las ejecuciones previas.
- Ruff/formato correctos, 35 archivos Python. Imagen backend construida; ayuda CLI
  comprobada sin red. Persistió únicamente la advertencia conocida Starlette/TestClient.
- Comprobado desde PowerShell el bind mount del comando recomendado: un contenedor
  temporal escribe texto sintético de ronda cero en .tools/diagnosticos, sin acceder
  al proveedor ni a MongoDB. No se han usado claves ni consumido saldo para probar.
- BANK_REFILL_ENABLED=false comprobado en Compose y backend habitual. Volumen de
  fase 3 y CA conservados. No se modifica .env, prompt activo, base o infraestructura.

### Archivos y alcance

Modificados backend/app/anthropic.py, app/generation.py,
scripts/preview_openrouter.py y tests/test_anthropic.py. Actualizados
COPIA_RESTAURACION.md, este devlog y seguimiento. No son necesarias dependencias
ni un ADR nuevo. Sin cambios frontend, README ni operaciones Git de escritura.
El autor debe repetir el ensayo de treinta para conocer el motivo real. Los
archivos de diagnóstico siguen ignorados por Git. El comando con run --rm y bind
mount evita perder el texto al retirar el contenedor temporal.