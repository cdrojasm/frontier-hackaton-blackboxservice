import { bucket, defineRailway, postgres, project, service } from "railway/iac";

// Source of truth for the Railway environment. Services use the repository root
// as build context, so both Dockerfiles can be kept under deploy/.
export default defineRailway((ctx) => {
  const production = ctx.environment === "production";
  const db = postgres("postgres");
  const artifacts = bucket("artifacts", { region: "iad" });

  const api = service("api", {
    healthcheck: "/health",
    replicas: production ? 2 : 1,
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/backend.Dockerfile",
      DATABASE_URL: db.env.DATABASE_URL,
      FRONTEND_ORIGIN: "*",
    },
  });

  const discoveryWorker = service("discovery-worker", {
    start: "python -m frontier_api.workers.discovery",
    replicas: production ? 2 : 1,
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/backend.Dockerfile",
      DATABASE_URL: db.env.DATABASE_URL,
      DISCOVERY_WORKER_POLL_INTERVAL_SECONDS: "2",
      DISCOVERY_REQUEST_TIMEOUT_SECONDS: "30",
      OPENROUTER_MODEL: "anthropic/claude-sonnet-4.5",
    },
  });

  // Mimic runs are processed independently from discovery runs. The worker is
  // intentionally private: the API creates a run in PostgreSQL and replicas
  // claim pending work with row-level locking.
  const mimicWorker = service("mimic-worker", {
    start: "python -m frontier_api.workers.mimic",
    replicas: production ? 2 : 1,
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/backend.Dockerfile",
      DATABASE_URL: db.env.DATABASE_URL,
      MIMIC_WORKER_POLL_INTERVAL_SECONDS: "2",
      MIMIC_MAX_ITERATIONS: "5",
      MIMIC_MATCH_THRESHOLD: "0.95",
      MIMIC_REQUEST_TIMEOUT_SECONDS: "30",
      MIMIC_OPENROUTER_MODEL: "openai/gpt-5.6-luna-pro",
      OPENROUTER_MODEL: "openai/gpt-4o-mini",
    },
  });

  const app = service("app", {
    healthcheck: "/health",
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/frontend.Dockerfile",
    },
  });

  return project("frontier", { resources: [db, artifacts, api, discoveryWorker, mimicWorker, app] });
});

