const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const ilan = require("../ilan");

// The portal's "TÜM İLANLAR" timeline as served on 2026-11-05 (first five days kept).
const html = fs.readFileSync(path.join(__dirname, "kamuilan-2026-11-05.html"), "utf8");

test("the timeline is read as institution, title and dates", () => {
  const rows = ilan.parse(html);
  assert.ok(rows.length >= 20, `${rows.length} satır`);
  const seker = rows.find((r) => r.kurum.startsWith("TÜRKİYE ŞEKER"));
  assert.equal(seker.title, "42 SÖZLEŞMELİ PERSONEL ALACAK");
  assert.equal(seker.range, "9 Kasım - 15 Kasım");
  // The session-bound "kod" of the link is not part of the key.
  assert.ok(!seker.key.includes("kod"));
  assert.equal(new Set(rows.map((r) => r.key)).size, rows.length);
});

test("only listings not seen before wake the phones; the first run wakes nobody", () => {
  const rows = ilan.parse(html);
  assert.deepEqual(ilan.fresh(rows, new Set(), true), []);
  const seen = new Set(rows.slice(1).map((r) => r.key));
  assert.deepEqual(ilan.fresh(rows, seen, false).map((r) => r.key), [rows[0].key]);
  assert.equal(ilan.fresh(rows, new Set(rows.map((r) => r.key)), false).length, 0);
});

test("no wake-up at night (23–07 Istanbul)", () => {
  assert.equal(ilan.quiet(new Date("2026-11-05T20:30:00Z")), true); // 23:30
  assert.equal(ilan.quiet(new Date("2026-11-05T03:59:00Z")), true); // 06:59
  assert.equal(ilan.quiet(new Date("2026-11-05T04:00:00Z")), false); // 07:00
  assert.equal(ilan.quiet(new Date("2026-11-05T19:59:00Z")), false); // 22:59
});

test("what is on the page now is remembered first, older keys up to the limit", () => {
  const rows = ilan.parse(html);
  const old = new Set(Array.from({ length: 2000 }, (_, i) => `eski|${i}`));
  const keep = ilan.remembered(rows, old);
  assert.equal(keep.length, ilan.KEEP);
  assert.deepEqual(keep.slice(0, rows.length), rows.map((r) => r.key));
  assert.equal(keep[keep.length - 1], "eski|1999");
});

test("the wake-up carries no personal data and expires in two hours", () => {
  const m = ilan.message(3);
  assert.equal(m.topic, "ilanlar");
  assert.deepEqual(m.data, { type: "listings", count: "3" });
  assert.equal(m.android.priority, "high");
  assert.equal(m.android.ttl, 2 * 3600 * 1000);
  assert.equal(m.notification, undefined);
});
