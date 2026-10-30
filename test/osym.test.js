const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const osym = require("../osym");

test("hoş geldiniz sayfası sonucu bulur", () => {
  const html = fs.readFileSync(path.join(__dirname, "hosgeldiniz-2026-10-30.html"), "utf8");
  const n = osym.welcome(html, "https://www.osym.gov.tr/hosgeldiniz.html", "2026-10-30");
  assert.equal(n.kind, "RESULT");
  assert.deepEqual(n.exams, ["kpss_onlisans"]);
  assert.equal(n.url, "https://sonuc.osym.gov.tr/Sorgu.aspx?SonucID=10397");
  const title = "2026-KPSS Ön Lisans Sınavı: Sınav Sonuçları Açıklandı";
  const listed = { title, day: "2026-10-30", kind: osym.kind(title), exams: osym.examsOf(title) };
  assert.equal(osym.signature(listed), osym.signature(n));
});

test("türler ve sınavlar", () => {
  assert.equal(osym.kind("KPSS-2026/5 Tercih Sonuçları Açıklandı"), "PLACEMENT");
  assert.deepEqual(osym.examsOf("KPSS-2026/5 Tercih Sonuçları"), ["kpss_lisans", "kpss_onlisans", "kpss_ortaogretim"]);
  assert.equal(osym.kind("Sonuçlarına İlişkin Sayısal Bilgiler"), null);
  assert.deepEqual(osym.examsOf("2026-EKPSS Sonuçları Açıklandı"), ["ekpss"]);
});

test("liste düzeni okunur", () => {
  const html = `<a class="duyuru-list-item" href="/TR,1/a.html" title="2026-KPSS Lisans Sınavı: Sınav Sonuçları Açıklandı" data-search-text="07.10.2026"></a>`;
  const [n] = osym.parse(html);
  assert.equal(n.day, "2026-10-07");
  assert.equal(n.url, "https://www.osym.gov.tr/TR,1/a.html");
  assert.deepEqual(n.exams, ["kpss_lisans"]);
});
