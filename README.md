# Frontier

Plataforma para descubrir el comportamiento de servicios caja negra y generar clones funcionalmente compatibles.

## Estructura

```text
src/
  frontier_api/
    domain/       # Entidades y reglas de negocio puras
    application/  # Casos de uso y puertos
    infra/
      entrypoint/ # HTTP/FastAPI y futuros workers
      factory/    # Composition root por servicio
      db/         # SQLAlchemy y modelos de persistencia
      repositories/
app/              # React + Vite
deploy/           # Dockerfiles, Nginx e IaC de Railway
db/               # Esquema inicial PostgreSQL
```

## Ejecutar la API localmente

```bash
cd .
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
PYTHONPATH=src uvicorn frontier_api.main:app --reload --port 8000
```

La API expone `GET /health`, `POST /api/v1/services` y `GET /api/v1/services/{id}`.

## Ejecutar el frontend localmente

```bash
cd app
npm install
npm run dev
```

## Desplegar en Railway

La definición de infraestructura vive en [deploy/railway.ts](deploy/railway.ts). El archivo `.railway/railway.ts` únicamente la expone al CLI de Railway.

```bash
railway link
railway config plan
railway config apply
```

Railway puede construir directamente los Dockerfiles desde el repositorio; no hace falta publicar imágenes en Docker Hub ni en otro registry.

La app frontend mantiene el proxy privado hacia `api.railway.internal`, pero Nginx lo resuelve de forma lazy para que el healthcheck no dependa de que la API esté lista durante el arranque.

### Discovery worker

El servicio `discovery-worker` ejecuta los `discovery_runs` en segundo plano. La API
crea un run en estado `created`; el worker lo reclama con PostgreSQL
`FOR UPDATE SKIP LOCKED`, ejecuta las `service_requests` asociadas y persiste el
progreso en `run_events`. Esto permite tener varias réplicas sin duplicar trabajo y
sin añadir Redis durante esta primera etapa.

Para probarlo localmente, levanta PostgreSQL, configura `DATABASE_URL` y ejecuta:

```bash
PYTHONPATH=src python -m frontier_api.workers.discovery
```

El worker bloquea por defecto destinos privados y loopback para reducir el riesgo de
SSRF. Para una prueba local explícita se puede usar `DISCOVERY_ALLOW_PRIVATE_TARGETS=true`.

Cuando el run no tiene más requests pendientes, el worker llama a OpenRouter para
decidir el siguiente probe. Configura `OPENROUTER_API_KEY` como secreto en Railway y
elige el modelo con `OPENROUTER_MODEL`. El agente ReAct devuelve una única acción
estructurada por iteración; el worker la valida, la ejecuta y vuelve a enviar la
evidencia. El discovery continúa hasta que el agente marque `done` o no produzca una probe nueva y válida; el costo se controla desde el presupuesto del proveedor/modelo y el intervalo del servicio.

El worker envía las credenciales activas del servicio como headers. También soporta
`DISCOVERY_AUTHORIZATION_TOKEN` como fallback para pruebas con
`Authorization: Bearer <token>`. Nunca se incluyen los valores de autorización en el
contexto enviado al modelo.

En Railway, `railway config plan` debe mostrar los servicios `api`, `app`,
`discovery-worker` y `mimic-worker`. Después de aplicar la configuración, ambos
workers no necesitan dominio público; solo comparten `DATABASE_URL` con la API.

### Mimic worker

El servicio `mimic-worker` sigue el mismo patrón durable que `discovery-worker`.
La API crea cada `mimic_run` en estado `created`; una réplica reclama el run con
PostgreSQL `FOR UPDATE SKIP LOCKED` y ejecuta la estrategia:

```text
created → generating → replaying → approval_required
                                      └─ (aprobado) → deploying → ready
```

Las réplicas son seguras porque el estado y los eventos viven en PostgreSQL. El
worker no expone puerto ni dominio público y se configura con:

- `MIMIC_WORKER_POLL_INTERVAL_SECONDS`: frecuencia de búsqueda de runs.
- `MIMIC_MAX_ITERATIONS`: máximo de ciclos generación/replay.
- `MIMIC_MATCH_THRESHOLD`: porcentaje mínimo para detener la iteración.
- `MIMIC_REQUEST_TIMEOUT_SECONDS`: timeout de cada replay.
- `MIMIC_OPENROUTER_MODEL`: modelo de código usado para construir los use cases.

El código generado debe persistirse en el bucket `artifacts`; el run permanece en
`approval_required` hasta que el usuario apruebe el resultado. El despliegue del
clon se mantiene separado de la generación para que ninguna réplica pueda hacer
ship automáticamente.

En la pantalla Discovery se puede cargar un CSV con estas columnas:

```csv
verbo,path,payload,response_code,response_content
GET,/health,,200,"{\"status\":\"ok\"}"
POST,/accounts,"{\"name\":\"demo\"}",201,"{\"id\":\"1\"}"
```

El archivo se importa junto con el `discovery_run` como evidencia inicial; después el
agente ReAct agrega probes adicionales automáticamente.

## Pendientes inmediatos

1. Migraciones versionadas con Alembic y ejecución pre-deploy en Railway.
2. Autenticación, tenancy y autorización por proyecto.
3. Worker de mimic y almacenamiento de artefactos/payloads en S3-compatible.
4. Integración con almacenamiento de artefactos y proveedor LLM.
5. Gateway `/mimic/{slug}/{proxy+}` y runtime multi-clon.
6. Tests unitarios, integración con PostgreSQL y CI.
7. Observabilidad, rate limits, SSRF protection y gestión de secretos.

