# FFmpeg Dropbox Split & Upload API (Railway v3.2, long-timeout)

**Why this build?**
Avoids `502 Bad Gateway` caused by long processing by increasing Gunicorn timeout to 900s.

**Deploy**
- Push to GitHub
- Railway → Deploy from GitHub
- Nixpacks installs python3.11 + ffmpeg
- Start: `gunicorn app:app --bind 0.0.0.0:$PORT --timeout 900 --workers 1 --threads 2`

**Call**
POST `/split-audio-upload` with the same JSON as v3.1.
Also increase your Make HTTP module Timeout to 600–900s for big files.