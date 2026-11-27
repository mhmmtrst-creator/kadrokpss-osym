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
# 2 (2026-11-23): a post offered in many places is written once with "places" (İçişleri 1170: one row per place
# went past the output limit). The apps read 1 and 2.
SCHEMA_VERSION = 2
# Talimat sürümü. Büyüdüğünde bütün açık ilanlar yeni talimatla bir kez daha okunur.
# 2 (2026-11-23, kullanıcı: "ne isteniyorsa o başlık altında yazsın", "tümü için geçerli"): ek tablolardaki bölüm ve
# belge adları tek tek yazılır, "Tablo-2'ye göre" gibi gönderme yasak; diğer şartlardaki 8 madde sınırı kalktı.
# 3 (2026-11-27, kullanıcı: "Evet, ekle"): ilanın bütün kadrolar için koyduğu genel şartlar (askerlik, güvenlik
# soruşturması, öğrenci yasağı, ikamet …) general_conditions alanına yazılır. Telefon ilanı PDF'i inmeden yalnızca bu
# okumayla değerlendirdiğinde (AiReading.alone) genel şartları buradan alır; yoksa askerliğini yapmamış bir adaya önce
# %100, PDF inince %90 deniyordu. Elle yazılmış ya da yeniden okunması riskli okumalar "korunsun" ile dışarıda kalır.
READING_VERSION = 3
# Aynı gün (yeniden okuma yapılmadan, yalnız yeni ilanlar için): iki yolla başvurulan kadro iki satır yazılır, başvuru
# evrakı "Belge:" diye yazılmaz (Jandarma 9000 elle düzeltildikten sonra, kullanıcı: "böyle sorunlar yaşamayalım").
# Dolar / milyon token, toplu işlem yarı fiyat (claude-api belgesindeki tablo, 2026-11-03).
PRICE = {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 2.5}
BATCH_DISCOUNT = 0.5
MONTHLY_LIMIT_USD = 100.0      # bu ay bu kadar harcandıysa yeni ilan gönderilmez (kullanıcı 2026-11-27: "40 değil 100 dolarlık apimiz var")
MAX_PER_RUN = 60               # bir çalıştırmada gönderilecek en çok ilan
MAX_PDF_BYTES = 25_000_000     # API sınırı 32 MB (base64 ile büyür)
MAX_ATTEMPTS = 4               # indirilemeyen ya da okunamayan bir ilan en çok bu kadar denenir
KEEP_DAYS = 45                 # portaldan kalkmış ilanların sonuçları bu kadar gün sonra silinir
# Akademik kadrolar (öğretim üyesi, araştırma görevlisi) uygulamada ayrı okunuyor; şimdilik maliyeti yarıya indirmek için atlanır.
SKIP_ACADEMIC = True
KEY = re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")
ACADEMIC = re.compile(r"ÖĞRETİM\s+(?:ÜYESİ|ELEMANI|GÖREVLİSİ)|ARAŞTIRMA\s+GÖREVLİSİ|AKADEMİK\s+PERSONEL", re.I)

SYSTEM = """You read Turkish public-sector job announcements (kamu personel alım ilanları) from kamuilan.sbb.gov.tr and extract every post they offer, exactly as the document states it, for an app that checks whether a candidate is eligible. Candidates pay to rely on this, so completeness and accuracy matter more than anything.

Rules:
1. Read every page and every table completely. A table may continue over several pages; a header repeated on a new page is not a row.
2. Every post with its own number of posts (pozisyon / kadro sayısı) must be read; never drop one. A merged cell (for example a position title, an education level or a qualification spanning several görev yeri rows) applies to each row it spans.
2a. One entry per kind of post (same title, specialty and conditions). When that post is offered in more than one place, list every place with its own number in places (one item per row or cell, in the document's order) and leave the entry's place and count null. When it is offered in one place only, use place and count and leave places empty.
2b. Some tables put the places in rows and the posts in columns (header rows with the post groups, their specialties and a KPSS puan türü row; then one row per il / birim with a number or "-" per column and total columns). Each non-empty cell is that column's post in that row's place: read every column of every row and every page; skip the total columns.
2c. A post that can be applied to in two ways (for example with a KPSS score and the listed bölüms, or without KPSS with a document such as an ustalık, kalfalık or MYK belgesi) is written as two entries: first the KPSS route; then the same title with ", KPSS'SİZ BELGE İLE" added before its closing parenthesis (or at its end), count null (the quota is shared), kpss_types empty, min_score null, any_department true when that route asks no bölüm, and that route's documents in other_conditions.
3. When the announcement has no table, take the posts from the text ("5 adet Büro Personeli", numbered groups, "A) ... için:" headings).
4. title: the position name as written (unvan / pozisyon adı), with its specialty in parentheses when the document gives one, e.g. "MÜHENDİS (MAKİNE)". place: the row's görev yeri / birim / il, or null.
5. count: the number of posts of that row as an integer; null only when the document gives none.
6. education: the levels the row accepts: ORTAOGRETIM (lise, ortaöğretim and equivalents), ONLISANS, LISANS, YUKSEK_LISANS, DOKTORA. List several when several are accepted.
7. kpss_types: KPSS score types written as P3, P93, P94, P1, P10, P48 and so on ("KPSSP3", "KPSS P3", "KPSS-P3" all mean P3). Empty when no KPSS score is asked.
8. department_kpss: when the accepted score type depends on the bölüm (for example İktisat P14-P16, İşletme P24-P26), one entry per bölüm with its own types; otherwise empty.
9. min_score: the lowest KPSS score accepted for the row, from the row or from the general conditions; else null. kpss_year: the year the KPSS score must come from.
10. departments: every bölüm / program / alan name the row accepts, one per item, copied as written. When the names stand in an annex table or list (Tablo-2, EK-1, "ekli listede yer alan programlar"), copy them from there: never write a reference such as "Tablo-2'deki programlar" instead of the names, however many there are. When a table marks with X (or ✓) which posts accept which program, give each post exactly the programs marked in its own column, from every page of the table and from every such table (lisans, ön lisans, ortaöğretim). Leave out level and length notes such as "(Lisans 4 Yıllık)" or "(Önlisans 2 Yıllık)" and write a program that is listed twice only once. any_department: true only when the row accepts any program of its level ("herhangi bir lisans programından mezun olmak"); departments is then empty.
11. gender: ERKEK or KADIN only when the row is open to one gender only; else null.
12. age_years and age_rule: the upper age limit that applies to the row and its exact wording ("30 yaşını bitirmemiş olmak", "35 yaşından gün almamış olmak"); else null.
13. license_classes: driving licence classes asked ("B", "C", "D", "E"); when two must be held together ("D ve C birlikte") write "D+C"; list alternatives separately.
14. other_conditions: every other requirement of the row, each as one short Turkish sentence that states the requirement itself, so that the candidate never has to open the announcement: certificates, licences and documents the candidate must already hold, with their exact names, each starting with "Belge: " ("Belge: Elektrik Tesisatçılığı ustalık belgesi"), experience, military service, language, health, height and weight with their numbers. Never write a reference ("Tablo-5'e göre", "ilanda belirtilen", "ekteki listede yer alan") without its content: copy the content instead. When the row may also be applied to with a document instead of KPSS (belge ile başvuru), write that route too and name every accepted document. Application paperwork (dilekçe, özgeçmiş, fotoğraf, başvuru formu, SGK hizmet dökümü, adli sicil kaydı, diploma or kimlik fotokopisi, taahhütname) is not a condition of the post: leave it out. Never write this schema's field names (departments, other_conditions) in the text. No limit on the number of items.
14a. Every condition the announcement sets for a post belongs to that post, in a field or in other_conditions: read the annexes (EK, Tablo, liste) a post refers to and bring their content into the post. Do not shorten or summarize lists to save space; the output limit is large.
15. declared_total: the total number of posts the announcement itself states ("181 sözleşmeli personel alınacaktır"); else null.
16. academic: true when the posts are academic staff (öğretim üyesi, öğretim görevlisi, araştırma görevlisi).
17. apply_start and apply_end: application dates as YYYY-MM-DD when stated; else null.
17a. general_conditions: every condition the announcement sets for ALL its posts in its general part ("Genel şartlar", "Başvuru şartları", the items of 657/48-A when they are written out, askerlik, güvenlik soruşturması / arşiv araştırması, the ban on being a student, residence, the KPSS year and the lowest score asked of everyone, the lower age limit …), each as one short Turkish sentence that states the requirement itself, as in other_conditions. Only what decides who may apply. Never the exam or selection procedure (the exam's date or place, how many candidates are called, the oral or written exam's pass mark, ranking and tie rules), how many places a candidate may choose, the identity documents to bring to the exam, or application paperwork; and never a condition that belongs to one post's own row. Empty when the announcement has no general part.
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
    "places": {"type": "array", "items": strict_object({"place": {"type": "string"}, "count": {"type": "integer"}})},
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
    "general_conditions": {"type": "array", "items": {"type": "string"}},
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


# ---------------------------------------------------------------- ilanın bağlantı verdiği belgeler
#
# Bazı ilanlarda kadrolar ilanın kendisinde değil, bağlantı verdiği bir kılavuzda ya da ektedir (2026-11-23: MSB astsubay
# temini "kılavuzu indirmek için tıklayınız", Sahil Güvenlik "www.sg.gov.tr adresindeki Bilgilendirme Kılavuzu"). İlk okuma
# hiç kadro bulamazsa ilan, bu belgeler bulunup eklenerek bir kez daha okunur.

MAX_LINKED = 2                 # bir ilana eklenecek en çok bağlantılı belge
MAX_LINKED_BYTES = 12_000_000  # bağlantılı bir belgenin en büyük boyutu
MAX_PAGES = 100                # bir istekteki toplam PDF sayfası
LINK_HOSTS = (".gov.tr", ".tsk.tr", ".edu.tr", ".bel.tr", ".pol.tr", ".k12.tr")
SKIP_HOSTS = {"kamuilan.sbb.gov.tr", "turkiye.gov.tr", "www.turkiye.gov.tr", "kariyerkapisi.gov.tr", "isealimkariyerkapisi.cbiko.gov.tr",
              "esube.iskur.gov.tr", "www.iskur.gov.tr", "resmigazete.gov.tr", "www.resmigazete.gov.tr", "www.osym.gov.tr", "ais.osym.gov.tr"}
URL_RE = re.compile(r"(?:https?://|www\.)[^\s\"'<>()\[\]]+", re.I)
GUIDE_WORDS = re.compile(r"k[ıi]lavuz|ilan|duyuru|\bek[-_ ]?\d|tablo|kontenjan|liste|dosyaindir|download|indir", re.I)
PAGE_LINK_WORDS = re.compile(r"memur|personel|al[ıi]m|ilan|k[ıi]lavuz|duyuru|temin|s[ıi]nav", re.I)
PAGE_WORDS = re.compile(r"kontenjan|kadro|tablo|\bek-?\s?\d|al[ıi]nacak|branş|unvan|pozisyon|say[ıi]s[ıi]", re.I)


def _norm(u: str) -> str:
    u = u.strip().rstrip(".,;:)")
    return "https://" + u if u.lower().startswith("www.") else u


def _allowed(u: str) -> bool:
    from urllib.parse import urlparse
    h = (urlparse(u).hostname or "").lower()
    return bool(h) and h.endswith(LINK_HOSTS) and h not in SKIP_HOSTS


def pdf_links(pdf: bytes) -> list:
    """Bir PDF'in bağlantıları: önce tıklanır bağlantıları ("tıklayınız"), sonra metninde yazan adresler; yalnız kamu siteleri."""
    import io
    from pypdf import PdfReader
    out = []
    try:
        r = PdfReader(io.BytesIO(pdf))
        for page in r.pages:
            for a in (page.get("/Annots") or []):
                act = a.get_object().get("/A")
                uri = act.get_object().get("/URI") if act else None
                if uri:
                    out.append(_norm(str(uri)))
        for page in r.pages:
            out += [_norm(u) for u in URL_RE.findall(page.extract_text() or "")]
    except Exception:
        return []
    return [u for u in dict.fromkeys(out) if _allowed(u)]


def _fetch(o, url: str):
    with o.open(url, timeout=60) as r:
        data = r.read(MAX_LINKED_BYTES + 1)
        ctype = r.headers.get("Content-Type", "") or ""
    return (None if len(data) > MAX_LINKED_BYTES else data), ctype


def _page_pdfs(o, url: str, html: bytes, depth: int) -> list:
    """Bir sayfadaki kılavuz/ek görünümlü PDF bağlantıları; yoksa (depth > 0) ilan/duyuru sayfalarına bir adım daha bakılır."""
    from urllib.parse import urljoin
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html.decode("utf-8", "replace"), "html.parser")
    pdfs, pages = [], []
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        text = squash(a.get_text())
        if not _allowed(href):
            continue
        low = href.lower()
        if low.endswith(".pdf") or "dosyaindir" in low or "download" in low:
            if GUIDE_WORDS.search(text + " " + href):
                pdfs.append(href)
        elif depth > 0 and PAGE_LINK_WORDS.search(text):
            pages.append(href)
    out = list(dict.fromkeys(pdfs))
    if not out and depth > 0:
        for p in list(dict.fromkeys(pages))[:4]:
            try:
                data, ctype = _fetch(o, p)
            except Exception:
                continue
            if data and "html" in ctype.lower():
                out += _page_pdfs(o, p, data, depth - 1)
            if len(out) >= MAX_LINKED:
                break
    return out


def linked_documents(o, pdf: bytes) -> list:
    """İlanın bağlantı verdiği kılavuz ve ekler: (adres, PDF) çiftleri, en çok MAX_LINKED tane."""
    found = []
    for url in pdf_links(pdf)[:6]:
        if len(found) >= MAX_LINKED:
            break
        try:
            data, ctype = _fetch(o, url)
        except Exception:
            continue
        if not data:
            continue
        if data[:5] == b"%PDF-":
            found.append((url, data))
            continue
        if "html" not in ctype.lower():
            continue
        for href in _page_pdfs(o, url, data, depth=1)[:3]:
            if len(found) >= MAX_LINKED:
                break
            try:
                d2, _ = _fetch(o, href)
            except Exception:
                continue
            if d2 and d2[:5] == b"%PDF-" and all(d2 != f[1] for f in found):
                found.append((href, d2))
    return found


def page_count(pdf: bytes) -> int:
    import io
    from pypdf import PdfReader
    try:
        return len(PdfReader(io.BytesIO(pdf)).pages)
    except Exception:
        return 0


def trimmed(pdf: bytes, room: int) -> bytes:
    """Sayfa sınırına sığmayan bir kılavuzun kadro tablolarının geçtiği sayfaları (ilk iki sayfa da), en çok [room] sayfa."""
    import io
    from pypdf import PdfReader, PdfWriter
    r = PdfReader(io.BytesIO(pdf))
    if len(r.pages) <= room:
        return pdf
    keep = [i for i, p in enumerate(r.pages) if i < 2 or PAGE_WORDS.search(p.extract_text() or "")][:room]
    w = PdfWriter()
    for i in keep:
        w.add_page(r.pages[i])
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


# ---------------------------------------------------------------- Claude

def request_params(pdf: bytes, institution: str, title: str, extra: list = (), cut: int = 0) -> dict:
    """cut: bu ilanın yanıtı kaç kez sınırda kesildi. Büyük belgede ve kesilmiş ilanda düşünme payı azaltılır ki
    yanıtın tamamı (yüzlerce bölüm adı) sınıra sığsın (Jandarma 9000, 2026-11-23: 100 bin tokenin 80 bini düşünmeye gitti)."""
    def doc(data: bytes) -> dict:
        return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": base64.standard_b64encode(data).decode("ascii")}}
    content = [doc(pdf)]
    for url, data in extra:
        content.append({"type": "text", "text": f"İlanın bağlantı verdiği belge: {url}"})
        content.append(doc(data))
    ask = "Bu ilanı oku ve bütün kadro satırlarını şemaya göre çıkar."
    if extra:
        ask += (" İlk belge ilanın kendisi; ötekiler ilanın bağlantı verdiği kılavuz ve ekler. Kadrolar ilanda değil de bu"
                " belgelerdeyse onlardan çıkar. Bu ilana ait olmayan (başka yıl, başka alım) bir belgeyi kullanma ve notlarda söyle.")
    content.append({"type": "text", "text": f"Kurum: {institution}\nİlan başlığı: {title}\n{ask}"})
    config = {"format": {"type": "json_schema", "schema": SCHEMA}}
    # Düşünme payı azaltılmaz: orta ayarda Jandarma 9000'in 45 kadrosu 10'a indi, İçişleri 1170'te hiç kadro yazılmadı
    # (2026-11-23). Yanıt sınırı 128 bin; çok büyük tablolar (X işaretli bölüm tabloları) ayrıca okunur.
    return {
        "model": MODEL,
        "max_tokens": 128000,
        "system": [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "output_config": config,
        "messages": [{"role": "user", "content": content}],
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
            at = json.loads(f.read_text(encoding="utf-8")).get("read_at")
            if isinstance(at, str) and at:
                index[f.stem] = at
        except ValueError:
            continue
    # The list's own format has not changed: it stays 1 (a file's format is in the file).
    (OUT / "liste.json").write_text(json.dumps({"schema": 1, "ilanlar": index}, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def month() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m")


def previous(lid: str):
    """Bu ilanın kayıtlı okuması (yoksa None)."""
    try:
        return json.loads((RESULTS / f"{lid}.json").read_text(encoding="utf-8"))["result"]
    except Exception:
        return None


def posts_and_total(r: dict) -> tuple:
    rows = r.get("positions") or []
    total = sum((p.get("count") or 0) + sum((x.get("count") or 0) for x in (p.get("places") or [])) for p in rows)
    return len(rows), total


def worse(new: dict, old: dict) -> bool:
    """Yeni okuma eskisinden belirgin biçimde eksik mi: kadro sayısı yarıdan aza indi ya da açıklanan toplamı tutturan eski
    okumanın yerine tutturamayan geldi."""
    n_new, t_new = posts_and_total(new)
    n_old, t_old = posts_and_total(old)
    if n_old and n_new * 2 < n_old:
        return True
    declared = new.get("declared_total") or old.get("declared_total")
    return bool(declared) and t_old == declared and t_new != declared


def stored(lid: str):
    """Bir ilanın yayımlanmış okuma dosyası (bütün kayıt), yoksa ya da bozuksa None."""
    try:
        return json.loads((RESULTS / f"{lid}.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def protected(lid: str) -> bool:
    """ "korunsun": true olan okuma yeniden okunmaz, üzerine de yazılmaz: elle yazılmış (Jandarma 9000, 72 satır) ya da
    yeniden okunması riskli (İçişleri 1170, 40 kadro / 750 yer) okumalar. Yeni talimat sürümü onları eskitmez."""
    r = stored(lid)
    return bool(r and r.get("korunsun"))


def postless(l: dict, state: dict) -> bool:
    """Okunmuş ama kadro bulunmamış, bağlantılı belgeleri henüz denenmemiş ilan."""
    if state["listings"].get(l["id"], {}).get("ekler_denendi"):
        return False
    r = stored(l["id"])
    if not r or r.get("korunsun") or not isinstance(r.get("result"), dict):
        return False
    return not r["result"].get("positions") and not r["result"].get("academic")


def stale(l: dict) -> bool:
    """Eski talimatla okunmuş ilan: yeni talimatla bir kez daha okunur (korunanlar hariç)."""
    r = stored(l["id"])
    return bool(r) and not r.get("korunsun") and r.get("reading", 1) < READING_VERSION


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
                if r.result.type == "succeeded" and r.result.message.stop_reason == "max_tokens":
                    meta["kesildi"] = meta.get("kesildi", 0) + 1
                print(f"Okunamadı ({r.result.type}): {meta.get('institution', lid)} - {meta.get('title', '')}")
                continue
            try:
                text = next(x.text for x in r.result.message.content if x.type == "text")
                result = json.loads(text)
                if not isinstance(result, dict) or not isinstance(result.get("positions"), list):
                    raise ValueError("şemaya uymayan yanıt")
            except (StopIteration, ValueError) as e:  # bir ilanın bozuk yanıtı ötekileri durdurmaz
                state["attempts"][lid] = state["attempts"].get(lid, 0) + 1
                print(f"Okunamadı (yanıt bozuk: {e}): {meta.get('institution', lid)} - {meta.get('title', '')}")
                continue
            if protected(lid):
                # Gönderildikten sonra korumaya alınmış: eldeki okuma kalır.
                print(f"Korunan okuma, üzerine yazılmadı: {meta.get('institution', lid)} - {meta.get('title', '')}")
                state["attempts"].pop(lid, None)
                continue
            old = previous(lid)
            if old is not None and worse(result, old):
                # Yeni okuma eskisinden belirgin biçimde eksik (Jandarma 9000'de 45 kadro 10'a inmişti): eskisi kalır.
                print(f"Yeni okuma eskisinden eksik, eskisi kaldı: {meta.get('institution', lid)} - {meta.get('title', '')}")
                state["attempts"].pop(lid, None)
                # Eski okuma bu talimat sürümüyle denenmiş sayılır; yoksa her çalıştırmada yeniden gönderilirdi (stale).
                # read_at değişmez: telefonlar aynı dosyayı yeniden indirmez.
                kept = stored(lid)
                if kept and kept.get("reading", 1) < READING_VERSION:
                    kept["reading"] = READING_VERSION
                    (RESULTS / f"{lid}.json").write_text(json.dumps(kept, ensure_ascii=False, indent=1), encoding="utf-8")
                continue
            record = {
                "schema": SCHEMA_VERSION, "id": lid, "institution": meta.get("institution", ""), "title": meta.get("title", ""),
                "range": meta.get("range", ""), "read_at": now_iso(), "model": r.result.message.model, "result": result,
                "reading": READING_VERSION,
            }
            if meta.get("ekler"):
                record["ekler"] = meta["ekler"]  # the guides and annexes read with it
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
        if (not (RESULTS / f"{l['id']}.json").exists() or postless(l, state) or stale(l)) and l["id"] not in pending
        and state["attempts"].get(l["id"], 0) < MAX_ATTEMPTS and not (SKIP_ACADEMIC and ACADEMIC.search(l["title"]))
        and not state["listings"].get(l["id"], {}).get("kesildi")
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
        meta = state["listings"].setdefault(l["id"], {})
        meta.update({"institution": l["institution"], "title": l["title"], "range": l["range"], "seen": today()})
        extra = []
        if (RESULTS / f"{l['id']}.json").exists() and (postless(l, state) or meta.get("ekler")):
            # İlk okuma kadro bulamadı: ilanın bağlantı verdiği kılavuz ve ekler bulunup birlikte okunur (bir kez denenir).
            meta["ekler_denendi"] = True
            room = MAX_PAGES - page_count(pdf)
            size = len(pdf)
            for url, data in linked_documents(o, pdf):
                if room <= 0:
                    break
                data = trimmed(data, room)
                # The request carries the PDFs in base64 (a third larger); the API takes at most 32 MB.
                if size + len(data) > 22_000_000:
                    continue
                room -= page_count(data)
                size += len(data)
                extra.append((url, data))
            if not extra and not stale(l):
                print(f"Kadro yok, bağlantılı belge de yok: {l['institution']} - {l['title']}")
                continue
            meta["ekler"] = [u for u, _ in extra]
            print(f"Bağlantılı belgelerle yeniden okunacak: {l['institution']} - {l['title']} ({len(extra)} belge)")
        requests.append({"custom_id": l["id"], "params": request_params(pdf, l["institution"], l["title"], extra, meta.get("kesildi", 0))})
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


def ai_status(l: dict, state: dict) -> str:
    """Bir ilanın okuma durumu, telefonlar için (2026-11-27, "Mac'in okuması öncelikli"): "okundu" (okuması yayımlandı),
    "okunuyor" (sırada ya da toplu işte; telefon en çok bir saat bekler), "okunmayacak" (akademik, yanıtı kesilmiş, denemeleri
    tükenmiş ya da ayın sınırı dolmuş: telefon beklemeden kendisi okur)."""
    lid = l["id"]
    if (RESULTS / f"{lid}.json").exists():
        return "okundu"
    if any(lid in p.get("ids", []) for p in state.get("pending", [])):
        return "okunuyor"
    if SKIP_ACADEMIC and ACADEMIC.search(l["title"]):
        return "okunmayacak"
    if state.get("listings", {}).get(lid, {}).get("kesildi") or state.get("attempts", {}).get(lid, 0) >= MAX_ATTEMPTS:
        return "okunmayacak"
    if state.get("spent", {}).get(month(), 0.0) >= MONTHLY_LIMIT_USD:
        return "okunmayacak"
    return "okunuyor"


def write_portal(listings: list, state: dict) -> None:
    """Portaldaki bütün ilanlar (akademikler ve okunmayanlar da), her birinin okuma durumuyla ("ai", bkz. ai_status):
    GitHub'daki sunucu telefonları bu listeye göre uyandırır (macliste.js), çünkü portal yurt dışındaki sunuculara 403
    veriyor; telefonlar da hangi ilanı Mac'in okumayacağını buradan öğrenir. Dosya yalnızca liste ya da bir durum
    değişince değişir; boş bir okuma (hata sayfası) son iyi listenin yerine yazılmaz."""
    if not listings or not RESULTS.parent.exists():
        return
    rows = sorted(({"id": l["id"], "institution": l["institution"], "title": l["title"], "range": l["range"], "ai": ai_status(l, state)}
                   for l in listings), key=lambda r: r["id"])
    text = json.dumps({"schema": 1, "ilanlar": rows}, ensure_ascii=False, indent=0, sort_keys=True)
    path = OUT / "portal.json"
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")


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
    if not a.sayfa and not a.kuru:
        # Önce bilinen durumla; okuma bitince (run) son durumla bir kez daha yazılır.
        write_portal(listings, state)
    if a.kuru:
        submit(None, state, listings, dry=True)
        return
    key_file = Path.home() / ".kadrokpss" / "anthropic_api_key.txt"
    raw = os.environ.get("ANTHROPIC_API_KEY") or (key_file.read_text(encoding="utf-8") if key_file.exists() else "")
    # Only the key itself: a file or secret may hold more pasted text (2026-11-23 a "curl" example under the key
    # made the header illegal, the run failed and the error printed the key into the log).
    m = KEY.search(raw)
    if not m:
        print("ANTHROPIC_API_KEY yok: hiçbir şey okunmadı")
        return
    os.environ["ANTHROPIC_API_KEY"] = m.group(0)
    import anthropic
    client = anthropic.Anthropic(api_key=m.group(0), max_retries=6)
    try:
        run(client, state, listings, a.bekle, portal=not a.sayfa)
    except Exception as e:  # the message never carries the key
        print("Okuma yarıda kaldı:", KEY.sub("[ANAHTAR]", f"{type(e).__name__}: {e}")[:300])
        save_state(state)
        if not a.sayfa:
            write_portal(listings, state)
        sys.exit(1)


def run(client, state: dict, listings: list, wait_minutes: int, portal: bool = True) -> None:
    collect(client, state)
    submit(client, state, listings, dry=False)
    # Günde iki kez çalışınca (Mac, 08:00 ve 14:00) sonuçlar aynı çalıştırmada toplanır; çoğu toplu iş birkaç dakikada biter.
    deadline = time.time() + wait_minutes * 60
    while state["pending"] and time.time() < deadline:
        time.sleep(30)
        collect(client, state, quiet=True)
    if state["pending"]:
        print("Toplu iş henüz bitmedi; sonuçlar bir sonraki çalıştırmada toplanacak")
    prune(state, {l["id"] for l in listings})
    save_state(state)
    if portal:
        write_portal(listings, state)
    print(f"Bu ay harcanan: {state['spent'].get(month(), 0.0):.2f} $")


if __name__ == "__main__":
    main()
