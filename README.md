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
- Gizli anahtar: depo ayarlarında `FIREBASE_SA` (Firebase hizmet hesabı JSON'u). Depoya asla dosya olarak eklenmez.
