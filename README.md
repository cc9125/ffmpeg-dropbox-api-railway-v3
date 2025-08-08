# FFmpeg Dropbox Split & Upload API (Railway v3)

POST `/split-audio-upload` with:
```json
{
  "url": "https://www.dropbox.com/s/<id>/meeting.wav?dl=0",
  "segment_time": 400,
  "overlap_seconds": 10,
  "format": "wav",
  "dropbox_token": "<DROPBOX_ACCESS_TOKEN>",
  "dest_root": "/test/wav",
  "group_prefix": "meetingA",
  "max_dirs": 5,
  "max_files_per_dir": 5
}
```
Uploads chunks to Dropbox subfolders 01..05, each max 5 files.

Deploy: Push to GitHub → Railway → Deploy from GitHub
