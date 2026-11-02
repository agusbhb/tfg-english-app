// La imagen oficial ejecuta este archivo solo al inicializar un volumen vacío.
const env = process.env;
for (const key of ["MONGO_APP_USER", "MONGO_APP_PASSWORD", "MONGODB_DB"]) {
  if (!env[key] || env[key].includes("CHANGE_ME")) {
    throw new Error(`Configura ${key} antes de inicializar MongoDB`);
  }
}
if (env.MONGO_TEST_MODE === "true") {
  // Solo la instancia efímera de pruebas: permite BDs trainer_test_<uuid>.
  db.getSiblingDB("admin").createUser({
    user: env.MONGO_APP_USER,
    pwd: env.MONGO_APP_PASSWORD,
    roles: [
      { role: "readWriteAnyDatabase", db: "admin" },
      { role: "dbAdminAnyDatabase", db: "admin" },
    ],
  });
} else {
  db.getSiblingDB(env.MONGODB_DB).createUser({
    user: env.MONGO_APP_USER,
    pwd: env.MONGO_APP_PASSWORD,
    roles: [{ role: "readWrite", db: env.MONGODB_DB }],
  });
}
