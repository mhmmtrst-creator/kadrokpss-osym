#!/usr/bin/env python3
"""
Yeni kamu ilanlarını Claude ile okur (GitHub Actions, .github/workflows/yapay-zeka.yml).

Her çalıştırmada:
  1. Daha önce gönderilmiş toplu işlerin (batch) sonuçları toplanır ve ilan başına ai/ilan/<kimlik>.json yazılır.
  2. Portalda (kamuilan.sbb.gov.tr) henüz okunmamış ilanlar indirilip yeni bir toplu işle Claude'a gönderilir.
     Sonuçları bir sonraki çalıştırmada gelir. Toplu işlem yarı fiyatınadır, çoğu bir saatten kısa sürer.

Kimlik, uygulamanın KamuIlanClient.contentId'siyle aynıdır: sha1("kurum|başlık|tarih aralığı")'nın ilk 10 baytı.
Uygulamalar bu dosyayı indirir. Dosya yoksa ya da indirilemezse telefon ilanı kendi okuyucusuyla okur.
Uygunluk karşılaştırması telefonda yapılır: buraya hiçbir kişisel bilgi gelmez, yalnızca herkese açık ilan belgeleri okunur.

Ortam: ANTHROPIC_API_KEY (GitHub'ın gizli ayarlarında). Yoksa hiçbir şey yapmadan çıkar.
Yerel deneme: python yapay_zeka/okuyucu.py --kuru   (portalı okur, ne gönderileceğini yazar, Claude'a bir şey göndermez)
"""
import argparse
import base64
import datetime as dt
import hashlib
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://kamuilan.sbb.gov.tr/"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ai"
RESULTS = OUT / "ilan"
STATE = OUT / "durum.json"

MODEL = "claude-sonnet-5-5"
SCHEMA_VERSION = 1
# Dolar / milyon token, toplu işlem yarı fiyat (claude-api belgesindeki tablo, 2026-11-03).
PRICE = {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 2.5}
BATCH_DISCOUNT = 0.5
MONTHLY_LIMIT_USD = 40.0       # bu ay bu kadar harcandıysa yeni ilan gönderilmez
MAX_PER_RUN = 60               # bir çalıştırmada gönderilecek en çok ilan
MAX_PDF_BYTES = 25_000_000     # API sınırı 32 MB (base64 ile büyür)
MAX_ATTEMPTS = 4               # indirilemeyen ya da okunamayan bir ilan en çok bu kadar denenir
KEEP_DAYS = 45                 # portaldan kalkmış ilanların sonuçları bu kadar gün sonra silinir
# Akademik kadrolar (öğretim üyesi, araştırma görevlisi) uygulamada ayrı okunuyor; şimdilik maliyeti yarıya indirmek için atlanır.
SKIP_ACADEMIC = True
ACADEMIC = re.compile(r"ÖĞRETİM\s+(?:ÜYESİ|ELEMANI|GÖREVLİSİ)|ARAŞTIRMA\s+GÖREVLİSİ|AKADEMİK\s+PERSONEL", re.I)

SYSTEM = """You read Turkish public-sector job announcements (kamu personel alım ilanları) from kamuilan.sbb.gov.tr and extract every post they offer, exactly as the document states it, for an app that checks whether a candidate is eligible. Candidates pay to rely on this, so completeness and accuracy matter more than anything.

Rules:
1. Read every page and every table completely. A table may continue over several pages; a header repeated on a new page is not a row.
2. Output one entry per table row that has its own number of posts (pozisyon / kadro sayısı). A merged cell (for example a position title, an education level or a qualification spanning several görev yeri rows) applies to each row it spans. Never merge rows of different places into one entry and never drop a row.
3. When the announcement has no table, take the posts from the text ("5 adet Büro Personeli", numbered groups, "A) ... için:" headings).
4. title: the position name as written (unvan / pozisyon adı), with its specialty in parentheses when the document gives one, e.g. "MÜHENDİS (MAKİNE)". place: the row's görev yeri / birim / il, or null.
5. count: the number of posts of that row as an integer; null only when the document gives none.
6. education: the levels the row accepts: ORTAOGRETIM (lise, ortaöğretim and equivalents), ONLISANS, LISANS, YUKSEK_LISANS, DOKTORA. List several when several are accepted.
7. kpss_types: KPSS score types written as P3, P93, P94, P1, P10, P48 and so on ("KPSSP3", "KPSS P3", "KPSS-P3" all mean P3). Empty when no KPSS score is asked.
8. department_kpss: when the accepted score type depends on the bölüm (for example İktisat P14-P16, İşletme P24-P26), one entry per bölüm with its own types; otherwise empty.
9. min_score: the lowest KPSS score accepted for the row, from the row or from the general conditions; else null. kpss_year: the year the KPSS score must come from.
10. departments: the bölüm / program names the row accepts, one per item, as written. any_department: true when any program of the level is accepted ("herhangi bir lisans programından mezun olmak").
11. gender: ERKEK or KADIN only when the row is open to one gender only; else null.
12. age_years and age_rule: the upper age limit that applies to the row and its exact wording ("30 yaşını bitirmemiş olmak", "35 yaşından gün almamış olmak"); else null.
13. license_classes: driving licence classes asked ("B", "C", "D", "E"); when two must be held together ("D ve C birlikte") write "D+C"; list alternatives separately.
14. other_conditions: other requirements of the row in short Turkish phrases (sertifika, boy-kilo, deneyim, askerlik, ...), at most 8.
15. declared_total: the total number of posts the announcement itself states ("181 sözleşmeli personel alınacaktır"); else null.
16. academic: true when the posts are academic staff (öğretim üyesi, öğretim görevlisi, araştırma görevlisi).
17. apply_start and apply_end: application dates as YYYY-MM-DD when stated; else null.
18. Never invent a value the document does not give. When the document holds no posts (a link to an outside guide, a cancellation), return no positions and say so in notes. Put any doubt, in short Turkish, in notes."""


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def strict_object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


POSITION = strict_object({
    "code": nullable({"type": "string"}),
    "title": {"type": "string"},
    "place": nullable({"type": "string"}),
    "count": nullable({"type": "integer"}),
    "education": {"type": "array", "items": {"type": "string", "enum": ["ORTAOGRETIM", "ONLISANS", "LISANS", "YUKSEK_LISANS", "DOKTORA"]}},
    "kpss_types": {"type": "array", "items": {"type": "string"}},
    "department_kpss": {"type": "array", "items": strict_object({"department": {"type": "string"}, "kpss_types": {"type": "array", "items": {"type": "string"}}})},
    "min_score": nullable({"type": "number"}),
    "departments": {"type": "array", "items": {"type": "string"}},
    "any_department": {"type": "boolean"},
    "gender": nullable({"type": "string", "enum": ["ERKEK", "KADIN"]}),
    "age_years": nullable({"type": "integer"}),
    "age_rule": nullable({"type": "string"}),
    "license_classes": {"type": "array", "items": {"type": "string"}},
    "other_conditions": {"type": "array", "items": {"type": "string"}},
})

SCHEMA = strict_object({
    "declared_total": nullable({"type": "integer"}),
    "academic": {"type": "boolean"},
    "kpss_year": nullable({"type": "integer"}),
    "apply_start": nullable({"type": "string"}),
    "apply_end": nullable({"type": "string"}),
    "positions": {"type": "array", "items": POSITION},
    "notes": {"type": "string"},
})


# ---------------------------------------------------------------- portal

def opener():
    o = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    o.addheaders = [("User-Agent", "Mozilla/5.0 (Linux; Android 14) Chrome/130 Mobile"), ("Referer", BASE)]
    return o


def squash(s: str) -> str:
    """Jsoup'un text() / ownText() gibi: boşluk dizileri (bölünmez boşluk dahil) tek boşluk, baş ve son kırpılır."""
    return re.sub(r"[ \t\n\f\r ]+", " ", s or "").strip()


def content_id(institution: str, title: str, rng: str) -> str:
    """Uygulamanın KamuIlanClient.contentId'si."""
    return hashlib.sha1(f"{institution.strip()}|{title.strip()}|{rng.strip()}".encode("utf-8")).hexdigest()[:20]


def parse_timeline(html: str) -> list:
    """Uygulamanın KamuIlanClient.parseTimeline'ı gibi: ul#nav2 içindeki a.xx bağlantıları."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select("ul#nav2 a.xx"):
        href = a.get("href") or ""
        if not href.startswith("ilanDetay"):
            continue
        p1 = a.select_one("p.alt_p1")
        p2 = a.select_one("p.alt_p2")
        institution = squash(p1.get_text()) if p1 else ""
        em = p2.find("em") if p2 else None
        rng = squash(em.get_text()).strip("() ") if em else ""
        title = squash("".join(p2.find_all(string=True, recursive=False))) if p2 else ""
        if not institution and not title:
            continue
        lid = content_id(institution, title, rng)
        if lid in seen:
            continue
        seen.add(lid)
        out.append({"id": lid, "href": href, "institution": institution, "title": title, "range": rng})
    return out


# ---------------------------------------------------------------- Claude

def request_params(pdf: bytes, institution: str, title: str) -> dict:
    return {
        "model": MODEL,
        "max_tokens": 64000,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": {"format": {"type": "json_schema", "schema": SCHEMA}},
        "messages": [{
            "role": "user",
            "content": [
                {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(pdf).decode("ascii")}},
                {"type": "text", "text": f"Kurum: {institution}\nİlan başlığı: {title}\nBu ilanı oku ve bütün kadro satırlarını şemaya göre çıkar."},
            ],
        }],
    }


def cost_of(usage) -> float:
    u = {"in": usage.input_tokens or 0, "out": usage.output_tokens or 0,
         "cache_read": usage.cache_read_input_tokens or 0, "cache_write": usage.cache_creation_input_tokens or 0}
    return sum(u[k] * PRICE[k] for k in u) / 1e6 * BATCH_DISCOUNT


# ---------------------------------------------------------------- durum

def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"pending": [], "attempts": {}, "spent": {}, "listings": {}}


def save_state(state: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    # Uygulamaların her eşitlemede bir kez indirdiği küçük liste: hangi ilanın okuması var, ne zaman okundu.
    # Telefon yalnızca listede olan ve kendisinde daha yenisi olmayan ilanların dosyasını indirir.
    index = {}
    for f in sorted(RESULTS.glob("*.json")) if RESULTS.exists() else []:
        try:
            index[f.stem] = json.loads(f.read_text(encoding="utf-8")).get("read_at", "")
        except ValueError:
            continue
    (OUT / "liste.json").write_text(json.dumps({"schema": SCHEMA_VERSION, "ilanlar": index}, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def month() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")


def collect(client, state: dict, quiet: bool = False) -> None:
    """Biten toplu işlerin sonuçlarını yazar; bitmeyenler bir sonraki çalıştırmaya kalır."""
    still = []
    for p in state["pending"]:
        b = client.messages.batches.retrieve(p["batch"])
        if b.processing_status != "ended":
            if not quiet:
                print(f"Toplu iş sürüyor: {len(p['ids'])} ilan, gönderildi {p['sent']}")
            still.append(p)
            continue
        got = 0
        for r in client.messages.batches.results(p["batch"]):
            lid = r.custom_id
            meta = state["listings"].get(lid, {})
            ok = r.result.type == "succeeded" and r.result.message.stop_reason not in ("refusal", "max_tokens")
            if r.result.type == "succeeded":
                state["spent"][month()] = round(state["spent"].get(month(), 0.0) + cost_of(r.result.message.usage), 4)
            if not ok:
                state["attempts"][lid] = state["attempts"].get(lid, 0) + 1
                print(f"Okunamadı ({r.result.type}): {meta.get('institution', lid)} - {meta.get('title', '')}")
                continue
            text = next(x.text for x in r.result.message.content if x.type == "text")
            record = {
                "schema": SCHEMA_VERSION, "id": lid, "institution": meta.get("institution", ""), "title": meta.get("title", ""),
                "range": meta.get("range", ""), "read_at": now_iso(), "model": r.result.message.model, "result": json.loads(text),
            }
            RESULTS.mkdir(parents=True, exist_ok=True)
            (RESULTS / f"{lid}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            state["attempts"].pop(lid, None)
            got += 1
        print(f"Toplu iş bitti: {got} / {len(p['ids'])} ilan okundu")
    state["pending"] = still


def submit(client, state: dict, listings: list, dry: bool) -> None:
    pending = {i for p in state["pending"] for i in p["ids"]}
    todo = [
        l for l in listings
        if not (RESULTS / f"{l['id']}.json").exists() and l["id"] not in pending and state["attempts"].get(l["id"], 0) < MAX_ATTEMPTS
        and not (SKIP_ACADEMIC and ACADEMIC.search(l["title"]))
    ][:MAX_PER_RUN]
    if not todo:
        print("Okunacak yeni ilan yok")
        return
    spent = state["spent"].get(month(), 0.0)
    if spent >= MONTHLY_LIMIT_USD:
        print(f"Bu ayın sınırı doldu ({spent:.2f} $ / {MONTHLY_LIMIT_USD} $): yeni ilan gönderilmedi")
        return
    if dry:
        for l in todo:
            print(f"  gönderilecek: {l['id']} | {l['institution']} | {l['title']} | {l['range']}")
        print(f"Kuru çalıştırma: {len(todo)} ilan gönderilecekti")
        return
    o = opener()
    o.open(BASE, timeout=60).read()  # oturum çerezi
    requests = []
    for l in todo:
        try:
            pdf = o.open(BASE + l["href"], timeout=120).read()
        except Exception as e:  # bir ilanın hatası ötekileri durdurmaz
            state["attempts"][l["id"]] = state["attempts"].get(l["id"], 0) + 1
            print(f"İndirilemedi: {l['institution']} - {e}")
            continue
        if b"%PDF" not in pdf[:1024] or len(pdf) > MAX_PDF_BYTES:
            state["attempts"][l["id"]] = state["attempts"].get(l["id"], 0) + 1
            print(f"PDF değil ya da çok büyük: {l['institution']} - {l['title']}")
            continue
        state["listings"][l["id"]] = {"institution": l["institution"], "title": l["title"], "range": l["range"], "seen": today()}
        requests.append({"custom_id": l["id"], "params": request_params(pdf, l["institution"], l["title"])})
        time.sleep(1.5)
    if not requests:
        return
    b = client.messages.batches.create(requests=requests)
    state["pending"].append({"batch": b.id, "ids": [r["custom_id"] for r in requests], "sent": now_iso()})
    print(f"Toplu iş gönderildi: {len(requests)} ilan")


def prune(state: dict, on_portal: set) -> None:
    """Portaldan kalkalı KEEP_DAYS günü geçen ilanların sonuçları silinir."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for lid, meta in list(state["listings"].items()):
        if lid in on_portal:
            meta["seen"] = today()
            continue
        if meta.get("seen", today())[:10] < cutoff:
            (RESULTS / f"{lid}.json").unlink(missing_ok=True)
            state["listings"].pop(lid, None)
            state["attempts"].pop(lid, None)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kuru", action="store_true", help="Claude'a bir şey göndermeden ne yapılacağını yazar")
    ap.add_argument("--sayfa", help="portal yerine kaydedilmiş bir liste sayfası (deneme)")
    ap.add_argument("--bekle", type=int, default=0, metavar="DAKİKA", help="gönderilen toplu işi en çok bu kadar dakika bekleyip sonuçları aynı çalıştırmada topla")
    a = ap.parse_args()
    try:
        html = Path(a.sayfa).read_text(encoding="utf-8") if a.sayfa else opener().open(BASE, timeout=60).read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Portal yurt dışındaki sunuculara (GitHub) 403 veriyor: hata sayılmaz, bu makineden okunamıyor demektir.
        print(f"Portal erişimi reddetti (HTTP {e.code}): bu makineden ilan okunamıyor")
        return
    listings = parse_timeline(html)
    print(f"Portalda {len(listings)} ilan")
    state = load_state()
    if a.kuru:
        submit(None, state, listings, dry=True)
        return
    key_file = Path.home() / ".kadrokpss" / "anthropic_api_key.txt"
    if not os.environ.get("ANTHROPIC_API_KEY") and key_file.exists():
        os.environ["ANTHROPIC_API_KEY"] = key_file.read_text(encoding="utf-8").strip()  # Mac'teki iş (launchd)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY yok: hiçbir şey okunmadı")
        return
    import anthropic
    client = anthropic.Anthropic(max_retries=6)
    collect(client, state)
    submit(client, state, listings, dry=False)
    # Günde iki kez çalışınca (Mac, 08:00 ve 14:00) sonuçlar aynı çalıştırmada toplanır; çoğu toplu iş birkaç dakikada biter.
    deadline = time.time() + a.bekle * 60
    while state["pending"] and time.time() < deadline:
        time.sleep(30)
        collect(client, state, quiet=True)
    if state["pending"]:
        print("Toplu iş henüz bitmedi; sonuçlar bir sonraki çalıştırmada toplanacak")
    prune(state, {l["id"] for l in listings})
    save_state(state)
    print(f"Bu ay harcanan: {state['spent'].get(month(), 0.0):.2f} $")


if __name__ == "__main__":
    main()
