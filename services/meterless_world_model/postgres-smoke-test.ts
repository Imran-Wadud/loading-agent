
import { WorldModel } from "./index.js";

async function main() {
  const wm = new WorldModel({
    storage: "postgres",
    namespace: "smoke-test",
  });

  try {
    await wm.ready();

    const result = await wm.upsertEntity({
      name: "PostgreSQL Persistence Test",
      type: "test",
      attrs: {
        updateTest: true,
      },
      source: {
        kind: "postgres-update-test",
        at: new Date().toISOString(),
      },
    });

    console.log("Updated entity:", result);
    console.log("PASS: update call completed.");
  } catch (error) {
    console.error("Update test failed:", error);
    process.exitCode = 1;
  } finally {
    await wm.close();
  }
}

main();
