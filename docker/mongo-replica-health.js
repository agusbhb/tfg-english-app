// Solo se ejecuta dentro del contenedor, autenticado como administrador.
const admin = db.getSiblingDB('admin');
if (!admin.auth(process.env.MONGO_INITDB_ROOT_USERNAME, process.env.MONGO_INITDB_ROOT_PASSWORD)) quit(1);
try {
  const status = rs.status();
  if (status.set !== process.env.MONGO_REPLICA_SET) quit(1);
} catch (error) {
  if (error.code !== 94) quit(1); // NotYetInitialized; no reconfigurar un set existente.
  rs.initiate({
    _id: process.env.MONGO_REPLICA_SET,
    members: [{ _id: 0, host: process.env.MONGO_REPLICA_HOST }],
  });
}
quit(admin.runCommand({ hello: 1 }).isWritablePrimary ? 0 : 1);
