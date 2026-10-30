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

test("hoş geldiniz sayfası ertesi gün tekrar bildirilmez, aynı günün iki tercih sonucu da kaybolmaz", () => {
  const list = { title: "2026-KPSS Ön Lisans Sınavı: Sınav Sonuçları Açıklandı", url: "https://www.osym.gov.tr/a", day: "2026-10-30" };
  list.kind = osym.kind(list.title); list.exams = osym.examsOf(list.title);
  const welcomeNextDay = { title: "2026-KPSS Ön Lisans Sınav Sonuçları Açıklandı", url: "https://sonuc.osym.gov.tr/Sorgu.aspx?SonucID=10397", day: "2026-10-31" };
  welcomeNextDay.kind = osym.kind(welcomeNextDay.title); welcomeNextDay.exams = osym.examsOf(welcomeNextDay.title);
  // Liste duyurusu dün bildirildi; ertesi gün hoş geldiniz sayfası hâlâ açık: yeni değil.
  const seen = new Set(osym.remembered([list]));
  assert.equal(osym.fresh([list, welcomeNextDay], seen, "2026-10-31", false).length, 0);
  // Aynı gün iki ayrı yerleştirme duyurusu: ikisi de yeni.
  const p5 = { title: "KPSS-2026/5 Tercih Sonuçları Açıklandı", url: "https://www.osym.gov.tr/p5", day: "2026-11-02" };
  const p6 = { title: "KPSS-2026/6 Tercih Sonuçları Açıklandı", url: "https://www.osym.gov.tr/p6", day: "2026-11-02" };
  for (const p of [p5, p6]) { p.kind = osym.kind(p.title); p.exams = osym.examsOf(p.title); }
  assert.equal(osym.fresh([p5, p6], new Set(), "2026-11-02", false).length, 2);
  // Eski bir duyuru, liste unutulmuş olsa bile yeni sayılmaz.
  const old = { ...list, url: "https://www.osym.gov.tr/eski", day: "2026-10-07" };
  assert.equal(osym.fresh([old], new Set(["x"]), "2026-10-31", false).length, 0);
});
