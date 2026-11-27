// ilan.message: telefonları uyandıran FCM mesajı. cheerio gerekmez (ilan.parse'ın testi ilan.test.js'te).
const test = require("node:test");
const assert = require("node:assert/strict");
const ilan = require("../ilan");
const maclist = require("../macliste");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

test("the wake-up carries no text, only the type and the count", () => {
  const m = ilan.message(3);
  assert.equal(m.topic, "ilanlar");
  assert.deepEqual(m.data, { type: "listings", count: "3" });
  assert.equal(m.notification, undefined);
  assert.deepEqual(m.android, { priority: "high", ttl: 2 * 3600 * 1000, collapseKey: "ilanlar" });
});

test("iPhones get it as a silent background push", () => {
  const now = new Date("2026-11-27T13:05:00Z");
  const m = ilan.message(1, now);
  // Apple refuses a content-available push that is not "background" with priority 5.
  assert.equal(m.apns.headers["apns-push-type"], "background");
  assert.equal(m.apns.headers["apns-priority"], "5");
  assert.equal(m.apns.headers["apns-expiration"], String(now.getTime() / 1000 + 2 * 3600));
  assert.deepEqual(m.apns.payload, { aps: { "content-available": 1 } });
  // No alert, sound or badge: the phone builds its own notification.
  assert.deepEqual(Object.keys(m.apns.payload.aps), ["content-available"]);
});

test("phones are woken once the reading is published, or at once when the Mac will not read the listing", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "macliste-"));
  fs.mkdirSync(path.join(dir, "ai"));
  fs.writeFileSync(path.join(dir, "ai", "durum.json"), JSON.stringify({ pending: [], listings: {} }));
  fs.writeFileSync(path.join(dir, "ai", "portal.json"), JSON.stringify({ schema: 1, ilanlar: [
    { id: "okunan", institution: "A KURUMU", title: "5 MEMUR ALACAK", range: "1 Aralık - 5 Aralık", ai: "okundu" },
    { id: "siradaki", institution: "B KURUMU", title: "2 MEMUR ALACAK", range: "1 Aralık - 5 Aralık", ai: "okunuyor" },
    { id: "akademik", institution: "C ÜNİVERSİTESİ", title: "4 ÖĞRETİM ELEMANI ALACAK", range: "1 Aralık - 5 Aralık", ai: "okunmayacak" },
    { id: "eski", institution: "D KURUMU", title: "1 MEMUR ALACAK", range: "1 Aralık - 5 Aralık" },
  ] }));
  assert.deepEqual(maclist.rows(dir).map((r) => r.id), ["okunan", "akademik", "eski"]);
});
