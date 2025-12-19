# vokal-oneri-backend

Basit bir FastAPI backend:
- `GET /health` -> servis ayakta mı?
- `POST /analyze` -> ses kaydından kullanıcı aralığını tahmin eder ve şarkı önerir.

## Local çalıştırma

Python 3.11 önerilir.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Kontrol:

```bash
curl http://localhost:8000/health
```

## Docker ile çalıştırma

```bash
docker build -t vokal-oneri-backend .
docker run -p 8000:8000 vokal-oneri-backend
```

## /analyze örnek çağrı (curl)

> `audio_url` herkes tarafından erişilebilir bir URL olmalı ("örn. public bir dosya linki").
> En az 30–45 saniye “la-la” kaydı önerilir.

```bash
curl -X POST http://localhost:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{
    "audio_url": "https://example.com/sample.wav",
    "user_is_premium": false,
    "songs": [
      {
        "id": "s1",
        "title": "Sample Song 1",
        "artist": "Sample Artist",
        "link": "https://open.spotify.com/track/xxxxxxxx",
        "minNote": 48,
        "maxNote": 67
      },
      {
        "id": "s2",
        "title": "Sample Song 2",
        "artist": "Sample Artist 2",
        "link": "https://www.youtube.com/watch?v=yyyyyyyy",
        "minNote": 52,
        "maxNote": 72
      }
    ]
  }'
```

Başarılı cevap örneği:

* `lowNoteMidi`, `highNoteMidi`: kullanıcının rahat aralığı (MIDI)
* `recommendations`: en uygun şarkı(lar)
