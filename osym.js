// ÖSYM duyurularını okuyup hangi sınavı ilgilendirdiğini bulan kurallar. Uygulamadaki
// app/content/OsymAnnouncements.kt ile aynı mantık; burada değiştirmek için uygulama güncellemesi gerekmez.
const cheerio = require("cheerio");

const SITE = "https://www.osym.gov.tr";
const LIST_URL = `${SITE}/Duyurular/Index`;
const HOME_URL = `${SITE}/`;

/** Türkçe harfleri sadeleştirir: "Sonuçları Açıklandı" → "sonuclari aciklandi". */
function fold(s) {
  return s.replace(/İ/g, "i").replace(/I/g, "ı").toLowerCase()
    .replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g").replace(/ü/g, "u").replace(/ö/g, "o").replace(/ç/g, "c")
    .replace(/â/g, "a").replace(/î/g, "i").replace(/û/g, "u");
}

function kind(title) {
  const t = fold(title);
  if (t.includes("sayisal bilgi") || t.includes("% 10") || t.includes("%10")) return null;
  if (/(yerlestirme|tercih)\s+sonuc\w*\s+aciklandi/.test(t)) return "PLACEMENT";
  if (/sonuc\w*\s+aciklandi/.test(t)) return "RESULT";
  if (/cevap anahtar\w*\s+yayim/.test(t)) return "ANSWER_KEY";
  if (/sinava giris belge\w*\s+erisime acildi/.test(t)) return "ENTRY_DOCUMENT";
  return null;
}

function examsOf(title) {
  const t = fold(title);
  const out = new Set();
  const ekpss = /(?<![a-z])ekpss/.test(t);
  if (ekpss) out.add("ekpss");
  if (!ekpss && /(?<![a-z])kpss/.test(t)) {
    if (t.includes("on lisans") || t.includes("onlisans")) out.add("kpss_onlisans");
    else if (t.includes("ortaogretim")) out.add("kpss_ortaogretim");
    else if (t.includes("lisans")) out.add("kpss_lisans");
    else if (!t.includes("dhbt")) ["kpss_lisans", "kpss_onlisans", "kpss_ortaogretim"].forEach((e) => out.add(e));
  }
  if (t.includes("dhbt")) out.add("dhbt");
  if (/(?<![a-z])yks(?![a-z])/.test(t) && !t.includes("ozel yetenek") && !t.includes("ozyes")) ["yks", "tyt", "ayt"].forEach((e) => out.add(e));
  if (/(?<![a-z])ales(?![a-z])/.test(t)) out.add("ales");
  if (/(?<![a-z])(e-)?yds(?![a-z])/.test(t)) out.add("yds");
  return [...out];
}

function dottedDay(text) {
  const m = /(\d{2})\.(\d{2})\.(\d{4})/.exec(text || "");
  return m ? `${m[3]}-${m[2]}-${m[1]}` : null;
}

function notice(title, url, day) {
  title = (title || "").replace(/\s+/g, " ").trim();
  if (!title || !/^https?:/.test(url || "")) return null;
  return { title, url, day, kind: kind(title), exams: examsOf(title) };
}

function abs(href, base) {
  if (!href) return null;
  try { return new URL(href, base).toString(); } catch { return null; }
}

/** Duyuru listesi ve ana sayfa kutusu. */
function parse(html, base = SITE) {
  const $ = cheerio.load(html);
  const out = [];
  $("a.duyuru-list-item[href]").each((_, el) => {
    const a = $(el);
    const title = a.attr("title") || a.find(".duyuru-list-title").text();
    out.push(notice(title, abs(a.attr("href"), base), dottedDay(a.attr("data-search-text"))));
  });
  $("li.announcement-item").each((_, el) => {
    const a = $(el).find("a[href]").first();
    if (!a.length) return;
    out.push(notice(a.attr("title") || a.text(), abs(a.attr("href"), base), dottedDay($(el).find(".announcement-date").text())));
  });
  const seen = new Set();
  return out.filter((n) => n && !seen.has(n.url) && seen.add(n.url));
}

/** Sonuç günü ana sayfanın yönlendirdiği "Hoş geldiniz" sayfası. */
function welcome(html, base, today) {
  const $ = cheerio.load(html);
  const title = ($(".wpage-title").first().text() || $("title").text()).replace(/\s+/g, " ").trim();
  if (!kind(title) || examsOf(title).length === 0) return null;
  const link = $("a[href*='sonuc.osym.gov.tr']").first().attr("href");
  return notice(title, abs(link, base) || `${SITE}/hosgeldiniz.html`, today);
}

/** Aynı duyuru iki yerden okunsa da tek bildirim: tür + sınavlar + gün. */
function signature(n) {
  return n.kind ? `sig:${n.kind}:${[...n.exams].sort().join(",")}:${n.day || ""}` : null;
}

module.exports = { SITE, LIST_URL, HOME_URL, fold, kind, examsOf, parse, welcome, signature };
