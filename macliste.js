// Kamu İlan listesi, Türkiye'deki Mac'in okuduğu haliyle (ai/durum.json, yapay_zeka/okuyucu.py).
// kamuilan.sbb.gov.tr yurt dışındaki sunuculara (GitHub) 403 veriyor. Mac portalı 15 dakikada bir okuyor, yeni
// ilanları Claude'a okutuyor ve listeyi okumalarla aynı kayıtta bu depoya yüklüyor. Telefonlar bu kayıttan sonra
// uyandırılır (watch.js runListings), böylece uyandıklarında yapay zekâ okuması hazırdır.
const fs = require("node:fs");
const path = require("node:path");

const squash = (s) => (s || "").replace(/\s+/g, " ").trim();

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

/**
 * Satırlar ilan.parse ile aynı biçimde ({ key, kurum, title, range } ve ilanın kimliği id). Liste ai/portal.json'dan
 * (portaldaki bütün ilanlar, akademikler de), o yoksa ai/durum.json'daki okunan ilanlardan gelir. Okuması süren (toplu
 * işte bekleyen) ilanlar henüz sayılmaz. İkisi de yoksa null: o zaman portal denenir.
 */
function rows(dir) {
  const state = readJson(path.join(dir, "ai", "durum.json"));
  const portal = readJson(path.join(dir, "ai", "portal.json"));
  const list = portal && Array.isArray(portal.ilanlar)
    ? portal.ilanlar
    : state && state.listings ? Object.entries(state.listings).map(([id, m]) => ({ id, ...m })) : null;
  if (!list) return null;
  const pending = new Set(((state && state.pending) || []).flatMap((p) => (p && p.ids) || []));
  const out = [];
  for (const m of list) {
    const id = m && m.id;
    if (!id || pending.has(id)) continue;
    const kurum = squash(m.institution);
    const title = squash(m.title);
    const range = squash(m.range).replace(/^\(\s*|\s*\)$/g, "");
    if (!kurum && !title) continue;
    out.push({ id, key: `${kurum}|${title}|${range}`.toLocaleLowerCase("tr"), kurum, title, range });
  }
  return out;
}

/** Yapay zekâ okumalarının zamanları (ai/liste.json: { ilanlar: { id: read_at } }); okunamazsa boş. */
function readAt(dir) {
  try {
    return JSON.parse(fs.readFileSync(path.join(dir, "ai", "liste.json"), "utf8")).ilanlar || {};
  } catch {
    return {};
  }
}

/**
 * Mac listesine geçilen ilk çalışmada (Firestore'da kayıt yokken) yalnızca son [withinMs] içinde okunmuş ilanlar yeni
 * sayılır: o gün çıkan ilanlar için telefonlar bir kez uyanır, eskiler için uyanmaz.
 */
function recent(list, times, now = new Date(), withinMs = 3 * 3600 * 1000) {
  return list.filter((r) => {
    const t = Date.parse(times[r.id] || "");
    return Number.isFinite(t) && now.getTime() - t <= withinMs && t <= now.getTime() + 60_000;
  });
}

module.exports = { rows, readAt, recent };
