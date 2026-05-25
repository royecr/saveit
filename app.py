"""
SaveIt Backend — YouTube / Facebook / Instagram / TikTok / Telegram downloader
Flask + yt-dlp + telethon
"""

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import yt_dlp
import tempfile
import os
import re
import urllib.parse
import subprocess
import sys

app = Flask(__name__)
CORS(app)


# ── Helpers ─────────────────────────────────────────────────────

def is_supported_url(url: str) -> bool:
    supported = [
        "youtube.com/", "youtu.be/",
        "facebook.com/", "fb.watch/", "fb.com/",
        "instagram.com/",
        "tiktok.com/",
    ]
    return any(s in url for s in supported)


def safe_filename(title: str) -> str:
    """Strip characters that are invalid in filenames."""
    return re.sub(r'[\\/*?:"<>|]', '', title).strip() or "video"


# ── /api/info ─────────────────────────────────────────────────────

@app.route("/api/info", methods=["POST"])
def info():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not is_supported_url(url):
        return jsonify({"error": "קישור לא נתמך. נסה YouTube, Facebook, Instagram או TikTok"}), 400

    ydl_opts = {
        "quiet"          : True,
        "noplaylist"     : True,
        "no_warnings"    : True,
        "extractor_args" : {"youtube": {"player_client": ["android"]}},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)

        return jsonify({
            "title"    : info_dict.get("title", ""),
            "thumbnail": info_dict.get("thumbnail", ""),
            "duration" : info_dict.get("duration", 0),
            "channel"  : info_dict.get("uploader", ""),
        })

    except yt_dlp.utils.DownloadError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Server error: {str(e)}"}), 500


# Quality presets for YouTube
YOUTUBE_QUALITY_FORMATS = {
    "360" : "best[height<=360][ext=mp4]/best[height<=360]",
    "480" : "best[height<=480][ext=mp4]/best[height<=480]",
    "720" : "best[height<=720][ext=mp4]/best[height<=720]",
    "1080": "best[height<=1080][ext=mp4]/best[height<=1080]",
}

# Facebook / Instagram / TikTok
SOCIAL_FORMAT = "best[ext=mp4]/best"


def get_format_string(url: str, quality: str) -> str:
    is_yt = "youtube.com/" in url or "youtu.be/" in url
    if is_yt:
        return YOUTUBE_QUALITY_FORMATS.get(quality, YOUTUBE_QUALITY_FORMATS["720"])
    else:
        return SOCIAL_FORMAT


# ── /api/download ─────────────────────────────────────────────────────

@app.route("/api/download")
def download():
    url     = (request.args.get("url")     or "").strip()
    quality = (request.args.get("quality") or "720").strip()
    title   = (request.args.get("title")   or "video").strip()

    if not url or not is_supported_url(url):
        return jsonify({"error": "Invalid URL"}), 400

    fname = safe_filename(title)

    if quality == "mp3":
        return _download_audio(url, fname)
    else:
        return _download_video(url, quality, fname)


def _download_video(url, quality, fname):
    """Download video to a temp file, then stream to client."""
    tmpdir = tempfile.mkdtemp()
    fmt = get_format_string(url, quality)

    ydl_opts = {
        "format"         : fmt,
        "outtmpl"        : os.path.join(tmpdir, "video.%(ext)s"),
        "noplaylist"     : True,
        "quiet"          : False,
        "no_warnings"    : False,
        "extractor_args" : {"youtube": {"player_client": ["android"]}},
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = os.listdir(tmpdir)
        if not files:
            return jsonify({"error": "Download failed — no file created"}), 500

        filepath = os.path.join(tmpdir, files[0])
        ext      = files[0].rsplit(".", 1)[-1]

        def stream_and_cleanup():
            try:
                with open(filepath, "rb") as f:
                    while chunk := f.read(65536):
                        yield chunk
            finally:
                try:
                    os.unlink(filepath)
                    os.rmdir(tmpdir)
                except Exception:
                    pass

        encoded = urllib.parse.quote(f"{fname}.{ext}")
        return Response(
            stream_and_cleanup(),
            mimetype=f"video/{ext}",
            headers={
                "Content-Disposition": f'attachment; filename="video.{ext}"; filename*=UTF-8\'\'{encoded}',
                "Content-Length"     : str(os.path.getsize(filepath)),
                "X-Accel-Buffering"  : "no",
            },
        )

    except Exception as e:
        try:
            for f in os.listdir(tmpdir):
                os.unlink(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


def _download_audio(url, fname):
    """Download audio as MP3."""
    tmpdir = tempfile.mkdtemp()

    ydl_opts = {
        "format"         : "bestaudio/best",
        "outtmpl"        : os.path.join(tmpdir, "audio.%(ext)s"),
        "noplaylist"     : True,
        "quiet"          : True,
        "no_warnings"    : True,
        "extractor_args" : {"youtube": {"player_client": ["android"]}},
        "postprocessors" : [{
            "key"            : "FFmpegExtractAudio",
            "preferredcodec" : "mp3",
            "preferredquality": "192",
        }],
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        files = [f for f in os.listdir(tmpdir) if f.endswith(".mp3")]
        if not files:
            return jsonify({"error": "Audio conversion failed"}), 500

        filepath = os.path.join(tmpdir, files[0])

        def stream_and_cleanup():
            try:
                with open(filepath, "rb") as f:
                    while chunk := f.read(65536):
                        yield chunk
            finally:
                try:
                    os.unlink(filepath)
                    os.rmdir(tmpdir)
                except Exception:
                    pass

        encoded = urllib.parse.quote(f"{fname}.mp3")
        return Response(
            stream_and_cleanup(),
            mimetype="audio/mpeg",
            headers={
                "Content-Disposition": f'attachment; filename="audio.mp3"; filename*=UTF-8\'\'{encoded}',
                "Content-Length"     : str(os.path.getsize(filepath)),
                "X-Accel-Buffering"  : "no",
            },
        )

    except Exception as e:
        try:
            for f in os.listdir(tmpdir):
                os.unlink(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


# ── /api/telegram-download ────────────────────────────────────────────

@app.route("/api/telegram-download")
def telegram_download():
    url = (request.args.get("url") or "").strip()

    if not url or "t.me/" not in url:
        return jsonify({"error": "קישור טלגרם לא תקין"}), 400

    # Check config exists
    try:
        from telegram_config import API_ID, API_HASH
    except ImportError:
        return jsonify({"error": "טלגרם לא מוגדר — ראה הוראות הגדרה"}), 503

    if not API_ID or not API_HASH:
        return jsonify({"error": "מלא API_ID ו-API_HASH בקובץ telegram_config.py"}), 503

    session_file = os.path.join(os.path.dirname(__file__), "telegram_session.txt")
    if not os.path.exists(session_file):
        return jsonify({"error": "לא מחובר לטלגרם — הפעל telegram_setup.py תחילה"}), 503

    with open(session_file, 'r') as f:
        session_string = f.read().strip()

    return _download_telegram(url, API_ID, API_HASH, session_string)


def _download_telegram(url, api_id, api_hash, session_string):
    """Download media from Telegram by running a separate Python worker process."""
    tmpdir = tempfile.mkdtemp()
    worker = os.path.join(os.path.dirname(__file__), "telegram_worker.py")

    try:
        result = subprocess.run(
            [sys.executable, worker, url, tmpdir],
            capture_output=True,
            text=True,
            timeout=300,  # מקסימום 5 דקות
        )

        if result.returncode != 0:
            err = result.stderr.strip().split('\n')[-1].replace("ERROR: ", "")
            return jsonify({"error": err or "הורדה נכשלה"}), 500

        filepath = result.stdout.strip()
        if not filepath or not os.path.exists(filepath):
            return jsonify({"error": "הורדה נכשלה — לא נמצא קובץ"}), 500

        if not filepath:
            return jsonify({"error": "הורדה נכשלה"}), 500

        ext  = filepath.rsplit('.', 1)[-1].lower() if '.' in filepath else 'mp4'
        name = os.path.basename(filepath)

        def stream_and_cleanup():
            try:
                with open(filepath, "rb") as f:
                    while chunk := f.read(65536):
                        yield chunk
            finally:
                try:
                    os.unlink(filepath)
                    os.rmdir(tmpdir)
                except Exception:
                    pass

        encoded = urllib.parse.quote(name)
        if ext in ('mp4', 'mov', 'avi', 'mkv', 'webm'):
            mime = f"video/{ext}"
        elif ext in ('mp3', 'm4a', 'ogg', 'flac', 'wav'):
            mime = f"audio/{ext}"
        else:
            mime = "application/octet-stream"

        return Response(
            stream_and_cleanup(),
            mimetype=mime,
            headers={
                "Content-Disposition": f'attachment; filename="telegram.{ext}"; filename*=UTF-8\'\'{encoded}',
                "Content-Length"     : str(os.path.getsize(filepath)),
                "X-Accel-Buffering"  : "no",
            },
        )

    except Exception as e:
        try:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


# ── Run ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
