import { bucket, defineRailway, postgres, preserve, project, service } from "railway/iac";

// Source of truth for the Railway environment. Services use the repository root
// as build context, so both Dockerfiles can be kept under deploy/.
export default defineRailway((ctx) => {
  const production = ctx.environment === "production";
  const shared = ctx.shared ?? {};
  const artifactEnv = {
    ARTIFACTS_BUCKET: shared.ARTIFACTS_BUCKET ?? "artifacts",
    ARTIFACTS_ENDPOINT: shared.ARTIFACTS_ENDPOINT ?? "",
    AWS_ACCESS_KEY_ID: shared.AWS_ACCESS_KEY_ID ?? "",
    AWS_SECRET_ACCESS_KEY: shared.AWS_SECRET_ACCESS_KEY ?? "",
    AWS_REGION: shared.AWS_REGION ?? "us-east-1",
  };
  const db = postgres("postgres");
  const artifacts = bucket("artifacts", { region: "iad" });

  const api = service("api", {
    healthcheck: "/health",
    replicas: production ? 2 : 1,
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/backend.Dockerfile",
      DATABASE_URL: db.env.DATABASE_URL,
      FRONTEND_ORIGIN: "*",
      MIMIC_RUNTIME_URL: "http://mimic-runtime.railway.internal",
    },
  });

  const discoveryWorker = service("discovery-worker", {
    start: "python -m frontier_api.workers.discovery",
    // Preserve both topology fields already present on this legacy service.
    // Omitting numReplicas makes IaC plan a change from 2 to null.
    deploy: {
      numReplicas: 2,
      multiRegionConfig: { iad: { numReplicas: 1 } },
    },
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/backend.Dockerfile",
      DATABASE_URL: db.env.DATABASE_URL,
      DISCOVERY_WORKER_POLL_INTERVAL_SECONDS: "2",
      DISCOVERY_REQUEST_TIMEOUT_SECONDS: "30",
      OPENROUTER_API_KEY: preserve(),
      OPENROUTER_MODEL: preserve(),
    },
  });

  // Mimic runs are processed independently from discovery runs. The worker is
  // intentionally private: the API creates a run in PostgreSQL and replicas
  // claim pending work with row-level locking.
  const mimicWorker = service("mimic-worker", {
    start: "python -m frontier_api.workers.mimic",
    replicas: production ? 2 : 1,
    deploy: { numReplicas: production ? 2 : 1, multiRegionConfig: { iad: { numReplicas: 1 } } },
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/backend.Dockerfile",
      DATABASE_URL: db.env.DATABASE_URL,
      MIMIC_WORKER_POLL_INTERVAL_SECONDS: "2",
      MIMIC_MAX_ITERATIONS: "5",
      MIMIC_MATCH_THRESHOLD: "0.95",
      MIMIC_REQUEST_TIMEOUT_SECONDS: "30",
      MIMIC_OPENROUTER_MODEL: "openai/gpt-5.6-luna-pro",
      OPENROUTER_API_KEY: preserve(),
      OPENROUTER_MODEL: preserve(),
      ...artifactEnv,
    },
  });

  // Long-lived runtime. It hot-loads the active artifact per slug/version;
  // publishing a new version never requires a Railway restart.
  const mimicRuntime = service("mimic-runtime", {
    start: "sh -c 'exec uvicorn frontier_api.runtime:app --host 0.0.0.0 --port ${PORT:-8000}'",
    replicas: production ? 2 : 1,
    deploy: { numReplicas: production ? 2 : 1, multiRegionConfig: { iad: { numReplicas: 1 } } },
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/backend.Dockerfile",
      DATABASE_URL: db.env.DATABASE_URL,
      MIMIC_RUNTIME_REFRESH_SECONDS: "0",
      ...artifactEnv,
    },
  });

  const app = service("app", {
    healthcheck: "/health",
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/frontend.Dockerfile",
    },
  });

  return project("frontier", { resources: [db, artifacts, api, discoveryWorker, mimicWorker, mimicRuntime, app] });
});

