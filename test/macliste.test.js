const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const maclist = require("../macliste");

function repo(state, index) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "macliste-"));
  fs.mkdirSync(path.join(dir, "ai"));
  if (state) fs.writeFileSync(path.join(dir, "ai", "durum.json"), JSON.stringify(state));
  if (index) fs.writeFileSync(path.join(dir, "ai", "liste.json"), JSON.stringify({ schema: 1, ilanlar: index }));
  return dir;
}

test("the Mac's list gives rows keyed as the portal's, without listings still being read", () => {
  const dir = repo({
    pending: [{ batch: "b1", ids: ["bekleyen"] }],
    listings: {
      f0adecaef054facf90e6: { institution: "GELİR İDARESİ BAŞKANLIĞI", title: "22 SÖZLEŞMELİ  AVUKAT ALACAK", range: "( 2 Aralık - 9 Aralık)", seen: "2026-11-27" },
      bekleyen: { institution: "BİR KURUM", title: "1 MEMUR ALACAK", range: "1 Aralık - 5 Aralık", seen: "2026-11-27" },
    },
  });
  const rows = maclist.rows(dir);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].id, "f0adecaef054facf90e6");
  assert.equal(rows[0].title, "22 SÖZLEŞMELİ AVUKAT ALACAK");
  assert.equal(rows[0].range, "2 Aralık - 9 Aralık");
  // ilan.parse's key: kurum|başlık|aralık in Turkish lower case.
  assert.equal(rows[0].key, "gelir idaresi başkanlığı|22 sözleşmeli avukat alacak|2 aralık - 9 aralık");
});

test("no file: null, so the portal is tried", () => {
  assert.equal(maclist.rows(repo(null)), null);
});

test("the first run counts only the readings of the last three hours", () => {
  const dir = repo(
    { pending: [], listings: { yeni: { institution: "A", title: "1 ALACAK", range: "x" }, eski: { institution: "B", title: "2 ALACAK", range: "y" } } },
    { yeni: "2026-11-27T13:02:28Z", eski: "2026-11-20T08:00:00Z" },
  );
  const fresh = maclist.recent(maclist.rows(dir), maclist.readAt(dir), new Date("2026-11-27T14:30:00Z"));
  assert.deepEqual(fresh.map((r) => r.id), ["yeni"]);
});

test("the portal list wins over the read ones, so academic listings wake the phones too", () => {
  const dir = repo({ pending: [{ batch: "b", ids: ["okunuyor"] }], listings: { okunan: { institution: "A", title: "1 ALACAK", range: "x" } } });
  fs.writeFileSync(path.join(dir, "ai", "portal.json"), JSON.stringify({ schema: 1, ilanlar: [
    { id: "okunan", institution: "A", title: "1 ALACAK", range: "x" },
    { id: "akademik", institution: "BİR ÜNİVERSİTESİ", title: "9 ÖĞRETİM ÜYESİ ALACAK", range: "1 Aralık - 15 Aralık" },
    { id: "okunuyor", institution: "C", title: "3 ALACAK", range: "z" },
  ] }));
  assert.deepEqual(maclist.rows(dir).map((r) => r.id), ["okunan", "akademik"]);
});
