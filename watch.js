// ÖSYM duyurularını okur; takip edilen bir sınavın sonucu, yerleştirmesi, cevap anahtarı veya giriş
// belgesi çıkınca o sınavı takip eden telefonlara (FCM konusu osym_<sınav>) anında haber verir.
// Telefondaki OsymWatchWorker yedek olarak çalışmaya devam eder; aynı duyuru telefonda iki kez bildirilmez.
// GitHub Actions (cli.js, 5 dakikada bir) ya da Firebase Functions (index.js) çalıştırır.
const admin = require("firebase-admin");
const osym = require("./osym");
const ilan = require("./ilan");

if (!admin.apps.length) admin.initializeApp();

const UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36 KadroKPSS";

async function get(url) {
  const r = await fetch(url, { headers: { "User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9" }, signal: AbortSignal.timeout(60_000) });
  if (!r.ok) throw new Error(`HTTP ${r.status} ${url}`);
  return { body: await r.text(), url: r.url };
}

function istanbulDay() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Istanbul" }).format(new Date());
}

async function read(today) {
  const [list, home] = await Promise.allSettled([get(osym.LIST_URL), get(osym.HOME_URL)]);
  const out = [];
  if (list.status === "fulfilled") out.push(...osym.parse(list.value.body));
  if (home.status === "fulfilled") {
    const w = osym.welcome(home.value.body, home.value.url, today);
    if (w) out.push(w);
    out.push(...osym.parse(home.value.body, home.value.url));
  }
  if (list.status === "rejected" && home.status === "rejected") throw list.reason;
  const seen = new Set();
  return out.filter((n) => !seen.has(n.url) && seen.add(n.url));
}

/** Bir duyuruyu ilgilendirdiği sınavların konularına gönderir (FCM koşulu en çok 5 konu alır). */
async function send(n) {
  const topics = n.exams.map((e) => `'osym_${e}' in topics`);
  for (let i = 0; i < topics.length; i += 5) {
    await admin.messaging().send({
      condition: topics.slice(i, i + 5).join(" || "),
      data: { type: "osym", title: n.title, url: n.url, day: n.day || "" },
      android: { priority: "high", ttl: 24 * 3600 * 1000 },
      apns: osym.apns(n),
    });
  }
}

async function run(log = console.log) {
  const today = istanbulDay();
  const notices = (await read(today)).filter((n) => n.kind && n.exams.length);
  const ref = admin.firestore().doc("server/osymWatch");
  const snap = await ref.get();
  const first = !snap.exists;
  const seen = new Set(snap.exists ? snap.get("seen") || [] : []);
  // İlk çalışmada yalnızca bugünkü duyurular haber sayılır.
  const fresh = osym.fresh(notices, seen, today, first).sort((a, b) => (b.day || "").localeCompare(a.day || ""));
  let sent = 0;
  for (const n of fresh.slice(0, 6)) {
    await send(n);
    sent++;
    log("Gönderildi:", n.title, n.exams.join(","));
  }
  // Sayfalarda şu an olan her şey hatırlanır; eskiler küme büyüyünce yer açar.
  const current = new Set(osym.remembered(notices));
  const older = [...seen].filter((x) => !current.has(x));
  const keep = [...current, ...older.slice(-Math.max(0, 1500 - current.size))];
  await ref.set({ seen: keep, at: admin.firestore.FieldValue.serverTimestamp() });
  log(`${notices.length} ilgili duyuru okundu, ${sent} yeni bildirim.`);
  // Kamu İlan bölümü ÖSYM'den bağımsız: biri okunamazsa öbürü yine çalışır.
  try {
    await runListings(log);
  } catch (e) {
    log("Kamu İlan okunamadı:", e.message || e);
  }
}

/**
 * Kamu İlan'da yeni ilan varsa "ilanlar" konusundaki telefonları uyandırır (ilan.js). Gece (23–07)
 * gönderilmez ve yeni ilanlar hatırlananlara eklenmez: sabahki ilk çalışma onları yine yeni sayar.
 */
async function runListings(log = console.log, now = new Date()) {
  const rows = ilan.parse((await get(ilan.BASE)).body);
  if (!rows.length) {
    log("Kamu İlan: liste boş okundu, bir şey yapılmadı.");
    return;
  }
  const ref = admin.firestore().doc("server/ilanWatch");
  const snap = await ref.get();
  const first = !snap.exists;
  const seen = new Set(snap.exists ? snap.get("seen") || [] : []);
  const fresh = ilan.fresh(rows, seen, first);
  if (fresh.length && ilan.quiet(now)) {
    log(`Kamu İlan: ${fresh.length} yeni ilan; gece, sabah bildirilecek.`);
    return;
  }
  if (fresh.length) {
    await admin.messaging().send(ilan.message(fresh.length));
    log(`Kamu İlan: ${fresh.length} yeni ilan, telefonlar uyandırıldı:`, fresh.slice(0, 3).map((r) => r.kurum).join("; "));
  } else {
    log(`Kamu İlan: ${rows.length} ilan okundu, yeni yok${first ? " (ilk çalışma)" : ""}.`);
  }
  await ref.set({ seen: ilan.remembered(rows, seen), at: admin.firestore.FieldValue.serverTimestamp() });
}

module.exports = { run, runListings };
