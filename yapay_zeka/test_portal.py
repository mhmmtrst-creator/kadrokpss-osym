"""ai/portal.json'daki okuma durumları (okuyucu.ai_status, write_portal). Claude'a ya da portala gitmez:
~/.kadrokpss/venv/bin/python -m unittest yapay_zeka/test_portal.py"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import okuyucu  # noqa: E402


def listing(lid: str, title: str = "5 MEMUR ALACAK") -> dict:
    return {"id": lid, "institution": "BİR KURUM", "title": title, "range": "1 Aralık - 5 Aralık", "href": "ilanDetay.aspx?kod=x"}


class PortalStatus(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.old = (okuyucu.OUT, okuyucu.RESULTS)
        okuyucu.OUT = Path(self.dir.name) / "ai"
        okuyucu.RESULTS = okuyucu.OUT / "ilan"
        okuyucu.RESULTS.mkdir(parents=True)
        self.state = {"attempts": {}, "listings": {}, "pending": [], "spent": {}}

    def tearDown(self):
        okuyucu.OUT, okuyucu.RESULTS = self.old
        self.dir.cleanup()

    def test_a_published_reading_is_okundu(self):
        (okuyucu.RESULTS / "a1.json").write_text("{}", encoding="utf-8")
        self.assertEqual("okundu", okuyucu.ai_status(listing("a1"), self.state))
        # Akademik başlıklı ya da yanıtı kesilmiş olsa da: okuması varsa okunmuştur.
        self.state["listings"]["a1"] = {"kesildi": 1}
        self.assertEqual("okundu", okuyucu.ai_status(listing("a1", "4 ÖĞRETİM ELEMANI ALACAK"), self.state))

    def test_queued_or_pending_is_okunuyor(self):
        self.assertEqual("okunuyor", okuyucu.ai_status(listing("yeni"), self.state))
        self.state["pending"] = [{"batch": "b1", "ids": ["bekleyen"], "sent": "2026-11-27T13:00:00Z"}]
        self.assertEqual("okunuyor", okuyucu.ai_status(listing("bekleyen"), self.state))
        self.state["attempts"]["yeni"] = okuyucu.MAX_ATTEMPTS - 1
        self.assertEqual("okunuyor", okuyucu.ai_status(listing("yeni"), self.state))

    def test_what_the_mac_gives_up_is_okunmayacak(self):
        self.assertEqual("okunmayacak", okuyucu.ai_status(listing("akademik", "15 ÖĞRETİM ÜYESİ ALACAK"), self.state))
        self.state["listings"]["kesik"] = {"kesildi": 1}
        self.assertEqual("okunmayacak", okuyucu.ai_status(listing("kesik"), self.state))
        self.state["attempts"]["bozuk"] = okuyucu.MAX_ATTEMPTS
        self.assertEqual("okunmayacak", okuyucu.ai_status(listing("bozuk"), self.state))
        # Ayın sınırı dolunca sıradaki ilanlar da okunmayacak; toplu işte bekleyen yine okunuyor.
        self.state["spent"][okuyucu.month()] = okuyucu.MONTHLY_LIMIT_USD
        self.state["pending"] = [{"batch": "b1", "ids": ["bekleyen"]}]
        self.assertEqual("okunmayacak", okuyucu.ai_status(listing("yeni"), self.state))
        self.assertEqual("okunuyor", okuyucu.ai_status(listing("bekleyen"), self.state))

    def test_the_file_changes_only_when_something_changes(self):
        rows = [listing("b2"), listing("a1"), listing("c3", "7 ÖĞRETİM ELEMANI ALACAK")]
        okuyucu.write_portal(rows, self.state)
        path = okuyucu.OUT / "portal.json"
        first = path.read_text(encoding="utf-8")
        data = json.loads(first)
        self.assertEqual(1, data["schema"])
        self.assertEqual(["a1", "b2", "c3"], [r["id"] for r in data["ilanlar"]])
        self.assertEqual(["okunuyor", "okunuyor", "okunmayacak"], [r["ai"] for r in data["ilanlar"]])
        self.assertEqual({"ai", "id", "institution", "range", "title"}, set(data["ilanlar"][0]))
        stamp = path.stat().st_mtime_ns
        okuyucu.write_portal(list(reversed(rows)), self.state)
        self.assertEqual(stamp, path.stat().st_mtime_ns)
        # Bir okuma yayımlanınca yalnızca o satır değişir.
        (okuyucu.RESULTS / "a1.json").write_text("{}", encoding="utf-8")
        okuyucu.write_portal(rows, self.state)
        self.assertEqual("okundu", json.loads(path.read_text(encoding="utf-8"))["ilanlar"][0]["ai"])
        # Boş bir liste (hata sayfası) son iyi listenin yerine yazılmaz.
        okuyucu.write_portal([], self.state)
        self.assertEqual(3, len(json.loads(path.read_text(encoding="utf-8"))["ilanlar"]))


if __name__ == "__main__":
    unittest.main()
