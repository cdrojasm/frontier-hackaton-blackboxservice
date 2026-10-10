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

## Pendientes inmediatos

1. Migraciones versionadas con Alembic y ejecución pre-deploy en Railway.
2. Autenticación, tenancy y autorización por proyecto.
3. Workers de discovery/mimic y una cola durable para trabajos largos.
4. Integración con almacenamiento de artefactos y proveedor LLM.
5. Gateway `/mimic/{slug}/{proxy+}` y runtime multi-clon.
6. Tests unitarios, integración con PostgreSQL y CI.
7. Observabilidad, rate limits, SSRF protection y gestión de secretos.

