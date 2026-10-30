// Firebase Functions ile çalıştırmak için (Blaze planı gerekir). Şimdilik GitHub Actions (cli.js) çalıştırıyor.
const { onSchedule } = require("firebase-functions/v2/scheduler");
const { logger } = require("firebase-functions");
const { run } = require("./watch");

exports.osymWatch = onSchedule(
  { schedule: "every 5 minutes", timeZone: "Europe/Istanbul", region: "europe-west1", memory: "256MiB", timeoutSeconds: 120, retryCount: 0 },
  () => run(logger.info),
);
