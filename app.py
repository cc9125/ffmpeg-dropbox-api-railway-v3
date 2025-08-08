
from flask import Flask, request, jsonify
import os
import subprocess
import uuid
import json
import time
import requests

app = Flask(__name__)

DROPBOX_CONTENT_UPLOAD = "https://content.dropboxapi.com/2/files/upload"
DROPBOX_LIST_FOLDER = "https://api.dropboxapi.com/2/files/list_folder"

def to_direct_dl(url: str) -> str:
    # Accept /s, /scl, or direct dl links
    if "dropboxusercontent.com" in url:
        return url
    if "dropbox.com" in url:
        u = url.replace("www.dropbox.com", "dl.dropboxusercontent.com")
        # strip dl=0 toggles
        u = u.replace("?dl=0", "").replace("&dl=0", "")
        return u
    return url

def ensure_dir_format(i: int) -> str:
    return f"{i:02d}"

def dropbox_list_count(token: str, path: str) -> int:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"path": path, "recursive": False, "include_deleted": False}
    try:
        resp = requests.post(DROPBOX_LIST_FOLDER, headers=headers, data=json.dumps(payload), timeout=30)
        if resp.status_code == 409:
            return 0
        resp.raise_for_status()
        data = resp.json()
        entries = data.get("entries", [])
        return sum(1 for e in entries if e.get(".tag") == "file")
    except Exception:
        return 0

def dropbox_upload(token: str, dest_path: str, content: bytes):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/octet-stream",
        "Dropbox-API-Arg": json.dumps({
            "path": dest_path,
            "mode": "add",
            "autorename": True,
            "mute": True,
            "strict_conflict": False
        })
    }
    resp = requests.post(DROPBOX_CONTENT_UPLOAD, headers=headers, data=content, timeout=120)
    resp.raise_for_status()
    return resp.json()

@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "ffmpeg-dropbox-api", "endpoints": ["/split-audio-upload"]})

@app.route("/split-audio-upload", methods=["POST"])
def split_audio_upload():
    data = request.get_json(silent=True) or {}
    dropbox_url = data.get("url")
    segment_time = int(data.get("segment_time", 400))
    overlap = int(data.get("overlap_seconds", 10))
    fmt = (data.get("format") or "wav").lower().strip(".")
    token = data.get("dropbox_token")
    dest_root = data.get("dest_root") or "/test/wav"
    group_prefix = data.get("group_prefix") or f"group-{uuid.uuid4().hex[:6]}"
    max_dirs = int(data.get("max_dirs", 5))
    max_per = int(data.get("max_files_per_dir", 5))

    if not dropbox_url or not ("dropbox.com" in dropbox_url or "dropboxusercontent.com" in dropbox_url):
        return jsonify({"error": "Missing or invalid Dropbox share URL", "url": dropbox_url}), 400
    if not token:
        return jsonify({"error": "Missing dropbox_token"}), 400

    dl_url = to_direct_dl(dropbox_url)
    work_id = uuid.uuid4().hex
    in_path = f"/tmp/in_{work_id}"
    out_dir = f"/tmp/splits_{work_id}"
    os.makedirs(out_dir, exist_ok=True)

    # Download
    try:
        subprocess.run(["curl", "-L", dl_url, "-o", in_path], check=True)
    except subprocess.CalledProcessError as e:
        return jsonify({"error": "Failed to download from Dropbox", "detail": str(e)}), 500

    # Duration (best-effort)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", in_path],
            check=True, capture_output=True, text=True
        )
        duration_s = float(probe.stdout.strip())
    except Exception:
        duration_s = None

    ext = fmt
    uploads = []
    seq = 1

    def pick_subdir():
        for i in range(1, max_dirs+1):
            sub = ensure_dir_format(i)
            folder_path = f"{dest_root}/{sub}"
            count = dropbox_list_count(token, folder_path)
            if count < max_per:
                return sub
        return None

    start = 0.0
    max_loops = 1000
    loops = 0
    while True:
        if duration_s is not None and start >= duration_s:
            break
        loops += 1
        if loops > max_loops:
            break

        # compute chunk length
        chunk = float(segment_time)
        if duration_s is not None:
            remaining = duration_s - start
            if remaining <= 0:
                break
            chunk = min(chunk, remaining)

        out_file = os.path.join(out_dir, f"segment-{seq:03d}.{ext}")
        # fast copy
        p = subprocess.run([
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-ss", str(max(0.0, start)),
            "-i", in_path,
            "-t", str(chunk),
            "-c", "copy",
            out_file
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if p.returncode != 0 or (not os.path.exists(out_file) or os.path.getsize(out_file) == 0):
            # fallback re-encode
            audio_codec = "aac" if ext in ("mp3","m4a","aac") else "pcm_s16le"
            cmd = [
                "ffmpeg", "-hide_banner", "-nostdin", "-y",
                "-ss", str(max(0.0, start)),
                "-i", in_path,
                "-t", str(chunk),
                "-map", "0:a:0", "-vn", "-c:a", audio_codec
            ]
            if audio_codec == "aac":
                cmd += ["-b:a", "128k"]
            cmd += [out_file]
            p2 = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if p2.returncode != 0 or (not os.path.exists(out_file) or os.path.getsize(out_file) == 0):
                return jsonify({"error": "ffmpeg segment failed", "detail": p2.stderr.decode(errors="ignore")[:4000]}), 500

        # pick folder & upload
        subdir = pick_subdir()
        if subdir is None:
            return jsonify({"error": "All destination subfolders are full", "dest_root": dest_root}), 409

        with open(out_file, "rb") as f:
            content = f.read()
        dest_path = f"{dest_root}/{subdir}/{group_prefix}-{seq:03d}.{ext}"
        try:
            _ = dropbox_upload(token, dest_path, content)
        except Exception as e:
            return jsonify({"error": "Dropbox upload failed", "path": dest_path, "detail": str(e)}), 502

        uploads.append({"dest": dest_path, "size": len(content)})
        seq += 1
        start += max(1.0, float(segment_time) - float(overlap))
        time.sleep(0.1)

    # cleanup
    try:
        os.remove(in_path)
    except Exception:
        pass

    return jsonify({
        "status": "success",
        "group_prefix": group_prefix,
        "uploaded_count": len(uploads),
        "uploaded": uploads,
        "watch_hint": f"Watch {dest_root}/01 in Make; API fills the lowest-numbered subfolder first (<{max_per} files)."
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
