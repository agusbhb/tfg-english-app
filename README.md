# TrAIner — Plataforma de aprendizaje de inglés con IA

TrAIner es una aplicación web educativa desarrollada como Trabajo de Fin de Grado en Ingeniería Informática en la Universidad del País Vasco (UPV/EHU).

Su objetivo es facilitar el aprendizaje de inglés mediante ejercicios interactivos adaptados a distintos niveles educativos, incorporando progresivamente inteligencia artificial para generar ejercicios y proporcionar feedback personalizado.

## Tecnologías utilizadas

- **Frontend:** Vue 3, JavaScript, Vite y Nginx.
- **Backend:** Python 3.12 y FastAPI.
- **Base de datos:** MongoDB.
- **Autenticación:** JWT y Argon2id.
- **Infraestructura:** Docker y Docker Compose.
- **Testing:** pytest, Vitest y herramientas de cobertura.
- **Integración continua:** GitHub Actions y SonarQube Cloud.

## Requisitos previos

Para ejecutar la aplicación es necesario tener instalado:

- Git.
- Docker Desktop, con el motor Docker en funcionamiento.

Los comandos siguientes están preparados para PowerShell, aunque Docker Compose también puede utilizarse desde otros sistemas operativos.

## Instalación y ejecución

### 1. Clonar el repositorio

```bash
git clone https://github.com/agusbhb/tfg-english-app.git
cd tfg-english-app
```

### 2. Configurar las variables de entorno

En Windows PowerShell, ejecutar:

```powershell
.\scripts\setup-env.ps1
```

Este script prepara la configuración local a partir del ejemplo disponible. Revisar las variables y credenciales de `.env` antes de arrancar.

El archivo `.env` contiene configuración privada y no debe subirse a GitHub.

### 3. Iniciar la aplicación

Con Docker Desktop en funcionamiento, ejecutar desde la raíz del proyecto:

```powershell
docker compose up -d --build --wait
```

Este comando construye las imágenes necesarias e inicia los servicios de la aplicación.

Para comprobar su estado:

```powershell
docker compose ps
```

Para consultar los registros:

```powershell
docker compose logs -f
```

### 4. Inicializar los datos educativos

Para cargar los datos iniciales de asignaturas, niveles, temas y ejemplos:

```powershell
docker compose exec backend python -m app.seed
```

El proceso de inicialización está diseñado para poder repetirse sin sobrescribir los documentos existentes.

### 5. Acceder a la aplicación

| Servicio | URL |
|---|---|
| Aplicación web | http://localhost:5173 |
| Documentación de la API | http://localhost:8000/docs |
| Mongo Express (opcional) | http://localhost:8081 |

Para iniciar Mongo Express:

```powershell
docker compose --profile tools up -d mongo-express
```

### 6. Detener la aplicación

```powershell
docker compose down
```

Este comando conserva el volumen de MongoDB y, por tanto, los datos almacenados.

**Importante:** evitar `docker compose down -v` salvo que se desee eliminar deliberadamente los volúmenes de datos.

## Ejecución de pruebas

El backend dispone de un entorno de pruebas Docker independiente de la base de datos de desarrollo.

Para ejecutar los tests:

```powershell
docker compose -f compose.test.yml up --build --abort-on-container-exit --exit-code-from tests
```

Para consultar posteriormente los resultados:

```powershell
docker compose -f compose.test.yml logs --tail 80 tests
```

Para limpiar los contenedores de pruebas:

```powershell
docker compose -f compose.test.yml down
```

Las pruebas del backend utilizan una instancia temporal de MongoDB, sin compartir el volumen de desarrollo.

## Organización del repositorio

```text
tfg-english-app/
├── .github/workflows/    # Integración continua
├── backend/              # API, modelos y pruebas
├── frontend/             # Aplicación Vue
├── docker/               # Inicialización de MongoDB
├── docs/                 # Documentación del proyecto
├── scripts/              # Scripts auxiliares
├── docker-compose.yml    # Entorno de desarrollo
├── compose.test.yml      # Entorno de pruebas
└── README.md
```

## Flujo de desarrollo

El proyecto utiliza dos ramas principales:

- **main:** código estable y validado.
- **develop:** desarrollo e integración de nuevas funcionalidades.

Los cambios se integran en `main` mediante Pull Requests, con comprobaciones automáticas de calidad y pruebas.

## Estado del proyecto

TrAIner se encuentra en desarrollo.

Las funcionalidades implementadas incluyen autenticación de usuarios, selección académica inicial y persistencia de datos educativos.

Las próximas iteraciones se centrarán en ejercicios interactivos, generación de contenido mediante IA, corrección y feedback personalizado, y seguimiento del progreso académico.

## Autor

Agustín Beltrán de Heredia

Trabajo de Fin de Grado — Ingeniería Informática

Universidad del País Vasco (UPV/EHU)