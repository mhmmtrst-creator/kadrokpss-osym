# KadroKPSS sunucu

- `osym.js`, `watch.js`, `cli.js`: ÖSYM duyurularını 5 dakikada bir okur, takip edilen sınavın duyurusu çıkınca
  Firebase Cloud Messaging ile o sınavın konusuna (`osym_<sınav>`) bildirim gönderir (`.github/workflows/osym.yml`).
- `app-config.json`: uygulamanın güncelleme gerektirmeden okuduğu ayarlar.
  - `premiumOpen`: `true` olunca Premium satışa açılır, ücretli özellikler kilitlenir.
  - `premiumLaunch`: Premium'un açıldığı gün (`2026-11-03` gibi). Kurucu üyelerin 3 ayı bu günden sayılır.
  - `foundersOpen`: `false` olunca yeni kurucu üye numarası verilmez.
  - `notice`: herkese bir kez gösterilecek duyuru, ör. `{"id": "n1", "title": "…", "text": "…", "url": "https://…"}`.
    Aynı duyuruyu yeniden göstermek için `id` değiştirilir.
  - `examSessions`: uygulamadaki takvimden sonra yayımlanan ÖSYM oturumları (2027 takvimi gibi), ör.
    `{"exam": "kpss_lisans", "name": "2027-KPSS Lisans", "applyStart": "2027-05-05T10:00", "applyEnd": "2027-05-14T23:59", "examAt": "2027-07-11T10:15", "resultAt": "2027-08-05T17:00"}`.
    Sınav kimlikleri: kpss_lisans, kpss_onlisans, kpss_ortaogretim, ekpss, dhbt, yks, tyt, ayt, ales, yds.
- `yapay_zeka/okuyucu.py`: yeni kamu ilanlarını Claude (Sonnet 5.5) ile okur ve her ilanın kadro satırlarını
  `ai/ilan/<kimlik>.json` olarak yayımlar. Portal GitHub'ın yurt dışı sunucularına 403 verdiği için iş KadroKPSS Mac'inde
  çalışır (launchd `app.kadro.yapay-zeka`, her gün 08:00 ve 14:00, `--bekle 55`); `.github/workflows/yapay-zeka.yml` yalnızca elle. Kimlik, uygulamanın
  `KamuIlanClient.contentId`'si ile aynıdır. Toplu işlem kullanılır (yarı fiyat): gönderilen ilanların sonuçları bir sonraki
  çalıştırmada ya da `--bekle` ile aynı çalıştırmada toplanır. Durum, harcama ve deneme sayıları `ai/durum.json`'dadır; ayda 40 doları geçince yeni ilan
  gönderilmez. Uygulamalar dosya yoksa ilanı kendi okuyucusuyla okur. Yalnızca herkese açık ilan belgeleri okunur;
  kullanıcıların bilgileri buraya gelmez. Gizli anahtar: `ANTHROPIC_API_KEY`.
- Gizli anahtar: depo ayarlarında `FIREBASE_SA` (Firebase hizmet hesabı JSON'u). Depoya asla dosya olarak eklenmez.
