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

  const app = service("app", {
    healthcheck: "/",
    env: {
      RAILWAY_DOCKERFILE_PATH: "/deploy/frontend.Dockerfile",
    },
  });

  return project("frontier", { resources: [db, artifacts, api, app] });
});

