// ÖSYM duyurularını okur; takip edilen bir sınavın sonucu, yerleştirmesi, cevap anahtarı veya giriş
// belgesi çıkınca o sınavı takip eden telefonlara (FCM konusu osym_<sınav>) anında haber verir.
// Telefondaki OsymWatchWorker yedek olarak çalışmaya devam eder; aynı duyuru telefonda iki kez bildirilmez.
// GitHub Actions (cli.js, 5 dakikada bir) ya da Firebase Functions (index.js) çalıştırır.
const admin = require("firebase-admin");
const osym = require("./osym");

if (!admin.apps.length) admin.initializeApp();

const UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/130.0 Mobile Safari/537.36 KadroKPSS";

async function get(url) {
  const r = await fetch(url, { headers: { "User-Agent": UA, "Accept-Language": "tr-TR,tr;q=0.9" }, signal: AbortSignal.timeout(60_000) });
  if (!r.ok) throw new Error(`ÖSYM HTTP ${r.status} ${url}`);
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
  const fresh = [];
  for (const n of notices) {
    const sig = osym.signature(n);
    if (seen.has(n.url) || seen.has(sig)) continue;
    // İlk çalışmada yalnızca bugünkü duyurular haber sayılır.
    if (!first || n.day === today) fresh.push(n);
    seen.add(n.url);
    seen.add(sig);
  }
  for (const n of fresh.slice(0, 6)) {
    await send(n);
    log("Gönderildi:", n.title, n.exams.join(","));
  }
  await ref.set({ seen: [...seen].slice(-1500), at: admin.firestore.FieldValue.serverTimestamp() });
  log(`${notices.length} ilgili duyuru okundu, ${fresh.length} yeni bildirim.`);
}

module.exports = { run };
