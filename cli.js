// GitHub Actions'ın 5 dakikada bir çalıştırdığı giriş (.github/workflows/osym.yml).
// Firebase hizmet hesabı anahtarı GOOGLE_APPLICATION_CREDENTIALS ile verilir.
require("./watch").run().catch((e) => {
  console.error(e);
  process.exit(1);
});
