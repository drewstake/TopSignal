const path = require("node:path");

// Apply last, after reading .env: a cloud configuration must never win here.
function offlineEnvironment(environment, repoRoot, { projectx = false } = {}) {
  const env = { ...environment };
  for (const key of Object.keys(env)) {
    if (/^(SUPABASE_|VITE_SUPABASE_|DATABENTO_API_KEY|PG)/i.test(key)
      || (!projectx && /^(PROJECTX_|TOPSTEP)/i.test(key))) {
      env[key] = "";
    }
  }
  const storageDir = path.join(repoRoot, "backend", "storage", "offline");
  return {
    ...env,
    PYTHON_DOTENV_DISABLED: "1",
    TOPSIGNAL_OFFLINE_DEV: "1",
    TOPSIGNAL_LOCAL_PROJECTX: projectx ? "1" : "0",
    TOPSIGNAL_ENV: "development",
    DATABASE_URL: `sqlite+pysqlite:///${path.join(storageDir, "topsignal.sqlite3").replaceAll("\\", "/")}`,
    MIGRATION_DATABASE_URL: "",
    AUTH_REQUIRED: "false",
    ALLOWED_ORIGINS: "http://127.0.0.1:5174",
    ALLOWED_ORIGIN_REGEX: "",
    DEFAULT_USER_ID: "00000000-0000-0000-0000-000000000000",
    DEFAULT_USER_EMAIL: "offline@topsignal.local",
    ALLOW_LEGACY_PROJECTX_ENV_CREDENTIALS: projectx ? "true" : "false",
    ALLOW_QUERY_BEARER_TOKENS: "false",
    CREDENTIALS_ENCRYPTION_KEY: projectx ? environment.CREDENTIALS_ENCRYPTION_KEY || "" : "",
    ALLOW_INSECURE_LOCAL_CREDENTIALS_KEY: "false",
    TOPSIGNAL_DB_SCHEMA_INIT: "full",
    TOPSIGNAL_BOT_WORKER_ENABLED: projectx ? "true" : "false",
    TOPSIGNAL_LIVE_EXECUTION_ENABLED: "false",
    TOPSIGNAL_BOT_WORKER_ALLOW_LIVE_EXECUTION: "false",
    PROJECTX_STREAMING_ENABLED: "false",
    JOURNAL_IMAGE_STORAGE_BACKEND: "local",
    JOURNAL_IMAGE_STORAGE_DIR: path.join(storageDir, "journal_images"),
    VITE_SUPABASE_URL: "",
    VITE_SUPABASE_ANON_KEY: "",
    VITE_OFFLINE_MODE: "true",
    VITE_LOCAL_PROJECTX: projectx ? "true" : "false",
    VITE_DEMO_MODE: "false",
  };
}

module.exports = { offlineEnvironment };
