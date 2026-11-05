// Kamu İlan portalının (kamuilan.sbb.gov.tr) ilan listesini okur. Yeni bir ilan görünce uygulamanın
// telefonlarına (FCM konusu "ilanlar") yalnızca "şimdi kontrol et" der: telefon ilanı kendisi indirir,
// kendi profiline göre değerlendirir ve bildirir; sunucuya hiçbir kişisel bilgi gelmez.
// Neden: pil tasarrufu (özellikle Xiaomi'nin varsayılan "Önerilen" ayarı) uygulama kapalıyken telefonun
// kendi kontrolünü durduruyor; yüksek öncelikli FCM mesajı bu durumda da telefonu uyandırabiliyor.
const cheerio = require("cheerio");

const BASE = "https://kamuilan.sbb.gov.tr/";
const TOPIC = "ilanlar";
/** Gece bu saatler arasında uyandırma gönderilmez; yeni ilanlar sabahki ilk çalışmada bildirilir. */
const QUIET_FROM = 23;
const QUIET_UNTIL = 7;
/** Hatırlanan ilan anahtarı sayısının üst sınırı. */
const KEEP = 800;

const squash = (s) => (s || "").replace(/\s+/g, " ").trim();

/**
 * "TÜM İLANLAR" listesinin satırları. Bağlantılardaki "kod" her oturumda değiştiği için bir ilanı
 * kurum + başlık + tarih aralığı tanır.
 */
function parse(html) {
  const $ = cheerio.load(html);
  const out = [];
  const seen = new Set();
  $("ul#nav2 a.xx").each((_, el) => {
    const a = $(el);
    if (!(a.attr("href") || "").startsWith("ilanDetay")) return;
    const kurum = squash(a.find("p.alt_p1").text());
    const p2 = a.find("p.alt_p2");
    const range = squash(p2.find("em").text()).replace(/^\(\s*|\s*\)$/g, "");
    const title = squash(p2.clone().children().remove().end().text());
    if (!kurum && !title) return;
    const key = `${kurum}|${title}|${range}`.toLocaleLowerCase("tr");
    if (seen.has(key)) return;
    seen.add(key);
    out.push({ key, kurum, title, range });
  });
  return out;
}

function istanbulHour(now = new Date()) {
  return Number(new Intl.DateTimeFormat("en-GB", { timeZone: "Europe/Istanbul", hour: "2-digit", hourCycle: "h23" }).format(now));
}

function quiet(now = new Date()) {
  const h = istanbulHour(now);
  return h >= QUIET_FROM || h < QUIET_UNTIL;
}

/** Hatırlananlarda olmayan satırlar; ilk çalışmada (kayıt yokken) hiçbir şey yeni sayılmaz. */
function fresh(rows, seen, first) {
  if (first) return [];
  return rows.filter((r) => !seen.has(r.key));
}

/** Şu an listede olanlar önce; eskiler sınır dolana kadar tutulur. */
function remembered(rows, seen) {
  const current = rows.map((r) => r.key);
  const now = new Set(current);
  const older = [...seen].filter((k) => !now.has(k));
  return [...current, ...older.slice(-Math.max(0, KEEP - current.length))];
}

/**
 * Yalnızca uyandırma: başlık ya da metin yok, telefon bildirimi kendisi kurar. Yüksek öncelik, uykudaki
 * telefonun mesajı hemen almasını sağlar; 2 saatlik ömür, gecikmiş bir uyandırmanın sonradan boşuna gelmesini önler.
 */
function message(count) {
  return {
    topic: TOPIC,
    data: { type: "listings", count: String(count) },
    android: { priority: "high", ttl: 2 * 3600 * 1000, collapseKey: "ilanlar" },
  };
}

module.exports = { BASE, TOPIC, KEEP, parse, quiet, fresh, remembered, message, istanbulHour };
