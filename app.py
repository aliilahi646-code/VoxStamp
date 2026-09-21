#!/usr/bin/env python3
"""
app.py -- Exact Timestamp Transcriber (browser-based, professional edition)
=============================================================================
Word-level exact timestamps via faster-whisper. No estimation, no interpolation.

LOCAL RUN:
    1) pip install -r requirements.txt
    2) ffmpeg installed and on PATH
    3) python app.py
    4) Open http://localhost:5000

ONLINE PUBLISH:
    See README_DEPLOY.md for free deployment on Render.com.
"""

import os
import sqlite3
import sys
import tempfile
import uuid
from functools import wraps

import requests
from flask import Flask, request, render_template_string, jsonify, session, redirect, url_for, g, send_file
from werkzeug.security import generate_password_hash, check_password_hash
from authlib.integrations.flask_client import OAuth

# When bundled into a Windows .exe (PyInstaller), files are extracted to a
# temporary folder at sys._MEIPASS, and the .exe itself lives wherever the
# person put it -- both need to be located explicitly rather than assumed
# relative to this script, the way they can be when just running "python
# app.py" normally.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)         # folder holding the .exe (for user data)
    RESOURCE_DIR = sys._MEIPASS                          # bundled files (for static/ etc.)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_DIR = BASE_DIR

app = Flask(__name__, static_folder=os.path.join(RESOURCE_DIR, "static"))
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB max upload
# Fixed secret key so login sessions survive server restarts (e.g. Render free
# tier sleeping/waking). For a personal/small-team tool this is fine; for a
# more sensitive deployment, set this from an environment variable instead.
app.secret_key = os.environ.get("SECRET_KEY", "voxstamp-fixed-secret-key-change-me-72f3a9")

# --- Google Sign-In (optional) ---------------------------------------------
# Only activates if GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are set as
# environment variables (see README_DEPLOY.md for how to get these from
# Google Cloud Console). Without them, email/password login still works
# fine -- the "Sign in with Google" button simply won't appear.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
GOOGLE_LOGIN_ENABLED = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

oauth = OAuth(app)
if GOOGLE_LOGIN_ENABLED:
    oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )

# In-memory job stores for Convert and Voiceover downloads (job_id -> file path).
# These reset if the server restarts -- fine for short-lived download links.
CONVERT_JOBS = {}
VOICEOVER_JOBS = {}
IMAGE_JOBS = {}

DB_PATH = os.path.join(BASE_DIR, "users.db")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT
        )
    """)
    conn.commit()
    conn.close()


init_db()


def login_required(view_func):
    """For page routes: redirect to the login page if not signed in."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def api_login_required(view_func):
    """For API/action routes called via fetch(): return a 401 JSON error
    instead of redirecting, so the page's own JS can react (send the person
    to /login) rather than receiving login-page HTML as if it were data."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return jsonify({"error": "Please log in to use this.", "login_required": True}), 401
        return view_func(*args, **kwargs)
    return wrapped


MODEL_CACHE = {}


def get_model(model_size: str):
    """Model is loaded once and cached in memory for fast repeat use."""
    if model_size not in MODEL_CACHE:
        from faster_whisper import WhisperModel
        MODEL_CACHE[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return MODEL_CACHE[model_size]


def _clean_word(w):
    """Lowercase and strip punctuation from a word for matching."""
    return "".join(c for c in w.lower() if c.isalnum())


def transcribe_exact(audio_path, model_size, max_words, pause_threshold, min_words, language, vocabulary_hint, isolate_terms):
    """
    Smart line-breaking, in priority order:
      0) Any word/phrase listed in isolate_terms (e.g. animal names, brand
         names) always becomes its OWN separate line, wherever it appears.
      1) Sentence/clause-ending punctuation (. , ? ! ; : and Urdu equivalents)
         -> always break here.
      2) A natural pause/silence between two words >= pause_threshold seconds
         -> break here (this is where the speaker actually pauses).
      3) max_words is only a SAFETY CAP -- if no natural pause or punctuation
         shows up for a long stretch (run-on speech), force a break so a
         line never gets absurdly long.
      4) min_words avoids breaking on a tiny 1-word fragment right after a
         short pause, unless it's a real sentence/clause end.
    Every timestamp is the real word-level start/end from the model --
    nothing here is estimated, only WHERE we cut is decided by these rules.
    """
    model = get_model(model_size)
    segments, info = model.transcribe(
        audio_path,
        word_timestamps=True,
        language=language or None,
        initial_prompt=vocabulary_hint or None,
    )

    words = []
    for segment in segments:
        if segment.words:
            words.extend(segment.words)

    # Parse isolate_terms ("lion, polar bear, wolf") into lists of cleaned
    # words, longest phrase first so "polar bear" matches before "bear" alone.
    isolate_phrases = []
    if isolate_terms:
        for term in isolate_terms.split(","):
            term = term.strip()
            if not term:
                continue
            phrase_words = [_clean_word(w) for w in term.split() if _clean_word(w)]
            if phrase_words:
                isolate_phrases.append(phrase_words)
        isolate_phrases.sort(key=len, reverse=True)

    lines = []
    current_words = []
    current_start = None

    def flush():
        nonlocal current_words, current_start
        if current_words:
            text = "".join(w.word for w in current_words).strip()
            lines.append({"start": current_start, "end": current_words[-1].end, "text": text})
        current_words = []
        current_start = None

    i = 0
    n = len(words)
    while i < n:
        # Check for an isolate-term match starting at position i.
        matched_phrase = None
        for phrase in isolate_phrases:
            plen = len(phrase)
            if i + plen > n:
                continue
            candidate = [_clean_word(words[i + k].word) for k in range(plen)]
            if candidate == phrase:
                matched_phrase = plen
                break

        if matched_phrase:
            flush()  # close whatever line was building before this entity
            entity_words = words[i:i + matched_phrase]
            text = "".join(w.word for w in entity_words).strip()
            lines.append({"start": entity_words[0].start, "end": entity_words[-1].end, "text": text})
            i += matched_phrase
            continue

        word = words[i]
        if current_start is None:
            current_start = word.start
        current_words.append(word)

        is_last_word = (i == n - 1)
        ends_sentence = word.word.strip().endswith((".", "?", "!", "۔", "؟"))
        ends_clause = word.word.strip().endswith((",", "،", ";", ":"))

        gap_to_next = None
        if not is_last_word:
            gap_to_next = words[i + 1].start - word.end
        natural_pause = gap_to_next is not None and gap_to_next >= pause_threshold

        hit_safety_cap = len(current_words) >= max_words
        enough_words = len(current_words) >= min_words

        should_break = (
            is_last_word
            or hit_safety_cap
            or (ends_sentence and enough_words)
            or (ends_clause and enough_words)
            or (natural_pause and enough_words)
        )

        if should_break:
            text = "".join(w.word for w in current_words).strip()
            lines.append({"start": current_start, "end": current_words[-1].end, "text": text})
            current_words = []
            current_start = None

        i += 1

    return lines, info.language


PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoxStamp -- Exact Timestamp Transcriber</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Ccircle cx='20' cy='20' r='17' fill='%23e8a33d'/%3E%3Cpath d='M11 21l5 5 13-13' stroke='%23241a08' stroke-width='3.4' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#0f1115">
<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div class="topbar">
    <div class="topbar-inner">
      <div class="stamp">
        <svg viewBox="0 0 40 40">
          <circle class="ring" cx="20" cy="20" r="17"/>
          <circle cx="20" cy="20" r="11" fill="var(--amber)"/>
          <path d="M14 20.5l4 4 8-8.5" stroke="var(--amber-ink)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="brand">VoxStamp</div>
      <div class="nav-tabs">
        <a href="/" class="active">Transcribe</a>
        <a href="/convert">Convert</a>
        <a href="/voiceover">Voiceover</a>
        <a href="/images">Images</a>
      </div>
      <div class="topbar-user">{% if user_email %}{{ user_email }} &middot; <a href="/logout">Log out</a>{% else %}<a href="/login">Log in</a>{% endif %}</div>
    </div>
  </div>

  <div class="wrap">
    <div class="install-banner" id="install-banner">
      <div class="install-banner-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#241a08" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
      </div>
      <div class="install-banner-text">
        <strong>Install VoxStamp as an app</strong>
        <span>Opens in its own window, works offline for the app shell, and sits on your desktop or home screen.</span>
      </div>
      <button id="install-app-btn" type="button">Install</button>
    </div>
    <div class="intro">
      <svg class="intro-watermark" viewBox="0 0 40 40">
        <circle cx="20" cy="20" r="17" fill="none" stroke="#e8a33d" stroke-width="2.2" stroke-dasharray="2.6 3.4"/>
        <circle cx="20" cy="20" r="11" fill="#e8a33d"/>
      </svg>
      <h1>Every word, timestamped exactly</h1>
      <p>No estimation, no interpolation -- each timestamp comes from real word-level audio alignment. Set your own line-breaking rules, edit the result, then export.</p>
    </div>

    <form id="f">
      <div class="layout">
        <div class="setup-col">
          <div class="dropzone" id="dropzone">
            <input type="file" name="audio" id="audio-input" required style="display:none">
            <div id="dropzone-empty">
              <div class="dropzone-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v1a7 7 0 0 0 14 0v-1"/><path d="M12 18v4M9 22h6"/></svg>
              </div>
              <div class="dropzone-title">Drop audio or video</div>
              <div class="dropzone-sub">or click to browse -- MP3, WAV, M4A, MP4</div>
            </div>
            <div class="dropzone-filename" id="dropzone-filename">
              <span id="dropzone-filename-text"></span>
              <span class="change-link">change file</span>
            </div>
          </div>

          <div class="panel">
            <div class="section-title">Line-breaking rules</div>
            <div class="row">
              <div>
                <label>Pause threshold (s)</label>
                <input type="number" name="pause_threshold" value="0.35" step="0.05" min="0.05">
              </div>
              <div>
                <label>Max words</label>
                <input type="number" name="max_words" value="14" min="2">
              </div>
            </div>
            <div class="row">
              <div>
                <label>Min words</label>
                <input type="number" name="min_words" value="2" min="1">
              </div>
              <div>
                <label>Model</label>
                <select name="model">
                  <option value="tiny">tiny (fastest)</option>
                  <option value="base">base</option>
                  <option value="small">small</option>
                  <option value="medium" selected>medium</option>
                  <option value="large-v3">large-v3</option>
                </select>
              </div>
            </div>
            <p class="hint">
              A line breaks at <b>sentence/clause end</b> (. , ? ! ; :), or wherever the
              speaker pauses for at least the pause threshold. Max words is only a
              safety cap for run-on speech with no natural pause.
            </p>

            <details>
              <summary>Advanced options</summary>
              <label>Language (blank = auto-detect)</label>
              <input type="text" name="language" placeholder="e.g. en, ur">
              <label>Vocabulary hint</label>
              <textarea name="vocabulary_hint" rows="2" placeholder="brand names, technical terms, or people's names that appear in this audio"></textarea>
              <label>Isolate as own line (comma-separated)</label>
              <input type="text" name="isolate_terms" placeholder="e.g. lion, polar bear, wolf">
              <p class="hint">Whenever any of these words appear, that word (or phrase) gets its own dedicated line.</p>
            </details>

            <button type="button" id="transcribe-btn" class="primary-btn play">Transcribe</button>
            <div id="status"></div>
            <div id="js-check" style="display:none;"></div>
          </div>
        </div>

        <div class="output-col">
          <div class="empty-state" id="empty-state">
            <svg class="empty-stamp" viewBox="0 0 40 40">
              <circle cx="20" cy="20" r="17" fill="none" stroke="#e8a33d" stroke-width="2.2" stroke-dasharray="2.6 3.4"/>
              <circle cx="20" cy="20" r="11" fill="#e8a33d"/>
              <path d="M14 20.5l4 4 8-8.5" stroke="#241a08" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <strong>Your transcript will appear here</strong>
            <span>Upload a file and transcribe -- each line will show its exact start and end time, ready to edit.</span>
          </div>

          <div id="result">
            <div class="player-header">
              <h3>Transcript <span id="line-count"></span></h3>
              <div class="format-chips">
                <button id="copy-btn" type="button">Copy all text</button>
                <button id="dl-srt" type="button">.SRT</button>
                <button id="dl-vtt" type="button">.VTT</button>
                <button id="dl-txt" type="button">.TXT</button>
              </div>
            </div>
            <div id="lines"></div>
          </div>
        </div>
      </div>
    </form>

    <div class="features">
      <div class="feature"><strong>Word-level timestamps</strong>Every line's timing comes from real audio alignment, not estimation.</div>
      <div class="feature"><strong>Editable before export</strong>Fix a name or typo directly in the transcript, then export.</div>
      <div class="feature"><strong>Your own line rules</strong>Break on pauses, punctuation, or specific words you choose.</div>
      <div class="feature"><strong>SRT / VTT / TXT</strong>Export in whichever format your workflow needs.</div>
    </div>
  </div>

<script src="/static/pwa.js"></script>
<script src="/static/app.js"></script>
</body>
</html>
"""


IMAGE_FORMATS = {"jpg", "jpeg", "png", "webp", "gif", "bmp"}
AUDIO_VIDEO_FORMATS = {"mp3", "wav", "m4a", "ogg", "flac", "aac", "mp4", "webm", "mov"}
AUDIO_ONLY_FORMATS = {"mp3", "wav", "m4a", "ogg", "flac", "aac"}
DOCUMENT_TARGET_FORMATS = {"docx", "pptx", "txt", "csv", "xlsx"}
ALL_CONVERT_FORMATS = IMAGE_FORMATS | AUDIO_VIDEO_FORMATS | DOCUMENT_TARGET_FORMATS


def convert_document(input_path, output_path, source_ext, target_format):
    """
    Document conversion -- pure Python, no LibreOffice/MS Office needed (so
    it works the same locally and on a plain web host). This moves TEXT
    CONTENT between formats; it does not preserve design, fonts, images,
    or slide layouts -- a Word doc becomes plain slide text in PPTX, and
    vice versa.
    """
    pair = (source_ext, target_format)

    if pair == ("docx", "pptx"):
        from docx import Document
        from pptx import Presentation
        doc = Document(input_path)
        prs = Presentation()
        layout = prs.slide_layouts[1]  # title + content
        slide = None
        body = None
        for para in doc.paragraphs:
            text = para.text.strip()
            if not text:
                continue
            is_heading = bool(para.style and para.style.name.startswith("Heading"))
            if is_heading or slide is None:
                slide = prs.slides.add_slide(layout)
                slide.shapes.title.text = text
                body = slide.placeholders[1].text_frame
                body.clear()
            else:
                if body.text:
                    body.add_paragraph().text = text
                else:
                    body.text = text
        if slide is None:
            slide = prs.slides.add_slide(layout)
            slide.shapes.title.text = os.path.splitext(os.path.basename(input_path))[0]
        prs.save(output_path)
        return

    if pair == ("pptx", "docx"):
        from pptx import Presentation
        from docx import Document
        prs = Presentation(input_path)
        doc = Document()
        for i, slide in enumerate(prs.slides, 1):
            doc.add_heading(f"Slide {i}", level=1)
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        text = para.text
                        if text.strip():
                            doc.add_paragraph(text)
        doc.save(output_path)
        return

    if pair == ("docx", "txt"):
        from docx import Document
        doc = Document(input_path)
        text = "\n".join(p.text for p in doc.paragraphs)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        return

    if pair == ("pptx", "txt"):
        from pptx import Presentation
        prs = Presentation(input_path)
        lines = []
        for i, slide in enumerate(prs.slides, 1):
            lines.append(f"--- Slide {i} ---")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        if para.text.strip():
                            lines.append(para.text)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return

    if pair == ("xlsx", "csv"):
        import csv
        import openpyxl
        wb = openpyxl.load_workbook(input_path, data_only=True)
        ws = wb.active
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            for row in ws.iter_rows(values_only=True):
                writer.writerow(["" if c is None else c for c in row])
        return

    if pair == ("csv", "xlsx"):
        import csv
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        with open(input_path, newline="", encoding="utf-8") as f:
            for row in csv.reader(f):
                ws.append(row)
        wb.save(output_path)
        return

    if pair == ("pdf", "txt"):
        from pypdf import PdfReader
        reader = PdfReader(input_path)
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text)
        return

    if pair == ("pdf", "docx"):
        from pypdf import PdfReader
        from docx import Document
        reader = PdfReader(input_path)
        doc = Document()
        for i, page in enumerate(reader.pages, 1):
            if i > 1:
                doc.add_page_break()
            doc.add_heading(f"Page {i}", level=2)
            text = page.extract_text() or ""
            for line in text.split("\n"):
                if line.strip():
                    doc.add_paragraph(line)
        doc.save(output_path)
        return

    if pair == ("pdf", "pptx"):
        from pypdf import PdfReader
        from pptx import Presentation
        reader = PdfReader(input_path)
        prs = Presentation()
        layout = prs.slide_layouts[1]  # title + content
        for i, page in enumerate(reader.pages, 1):
            text = (page.extract_text() or "").strip()
            slide = prs.slides.add_slide(layout)
            slide.shapes.title.text = f"Page {i}"
            body = slide.placeholders[1].text_frame
            # A slide can only hold so much text legibly; keep it generous
            # but bounded so PyPowerPoint doesn't choke on huge PDF pages.
            body.text = text[:2500] if text else "(no extractable text on this page)"
        prs.save(output_path)
        return

    raise RuntimeError(
        f"Converting .{source_ext} to .{target_format} isn't supported. "
        f"Document conversion currently supports: DOCX <-> PPTX (text "
        f"content only, no design/formatting), DOCX/PPTX -> TXT, "
        f"XLSX <-> CSV, and PDF -> TXT/DOCX/PPTX."
    )


def convert_file(input_path, output_path, target_format):
    """Convert a file. Images go through Pillow, documents through pure
    Python libraries, everything else (audio/video) through ffmpeg."""
    if target_format in IMAGE_FORMATS:
        from PIL import Image
        img = Image.open(input_path)
        save_format = "JPEG" if target_format in ("jpg", "jpeg") else target_format.upper()
        if save_format == "JPEG" and img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        img.save(output_path, format=save_format)
        return

    if target_format in DOCUMENT_TARGET_FORMATS:
        source_ext = os.path.splitext(input_path)[1].lstrip(".").lower()
        convert_document(input_path, output_path, source_ext, target_format)
        return

    import subprocess
    cmd = ["ffmpeg", "-y", "-i", input_path]
    # For audio-only output formats, drop any video stream so e.g. an mp4
    # converts cleanly to mp3 instead of failing or carrying video along.
    if target_format in AUDIO_ONLY_FORMATS:
        cmd += ["-vn"]
    cmd += [output_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if result.returncode != 0:
        # ffmpeg's error output is verbose; keep the last part, it usually
        # has the actual reason.
        tail = result.stderr.strip().splitlines()[-6:]
        raise RuntimeError("\n".join(tail))


CONVERT_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoxStamp -- Convert</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Ccircle cx='20' cy='20' r='17' fill='%23e8a33d'/%3E%3Cpath d='M11 21l5 5 13-13' stroke='%23241a08' stroke-width='3.4' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#0f1115">
<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div class="topbar">
    <div class="topbar-inner">
      <div class="stamp">
        <svg viewBox="0 0 40 40">
          <circle class="ring" cx="20" cy="20" r="17"/>
          <circle cx="20" cy="20" r="11" fill="var(--amber)"/>
          <path d="M14 20.5l4 4 8-8.5" stroke="var(--amber-ink)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="brand">VoxStamp</div>
      <div class="nav-tabs">
        <a href="/">Transcribe</a>
        <a href="/convert" class="active">Convert</a>
        <a href="/voiceover">Voiceover</a>
        <a href="/images">Images</a>
      </div>
      <div class="topbar-user">{% if user_email %}{{ user_email }} &middot; <a href="/logout">Log out</a>{% else %}<a href="/login">Log in</a>{% endif %}</div>
    </div>
  </div>

  <div class="wrap">
    <div class="install-banner" id="install-banner">
      <div class="install-banner-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#241a08" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
      </div>
      <div class="install-banner-text">
        <strong>Install VoxStamp as an app</strong>
        <span>Opens in its own window, works offline for the app shell, and sits on your desktop or home screen.</span>
      </div>
      <button id="install-app-btn" type="button">Install</button>
    </div>
    <div class="intro">
      <h1>Convert audio &amp; video files</h1>
      <p>Drop in a file, pick the format you need, and get a ready-to-use file back. Runs on ffmpeg -- no upload to a third-party converter.</p>
    </div>

    <form id="f">
      <div class="layout">
        <div class="setup-col">
          <div class="dropzone" id="dropzone">
            <input type="file" name="file" id="audio-input" required style="display:none">
            <div id="dropzone-empty">
              <div class="dropzone-icon">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
              </div>
              <div class="dropzone-title">Drop any file</div>
              <div class="dropzone-sub">audio, video, image, or document -- click to browse</div>
            </div>
            <div class="dropzone-filename" id="dropzone-filename">
              <span id="dropzone-filename-text"></span>
              <span class="change-link">change file</span>
            </div>
          </div>

          <div class="panel">
            <div class="section-title">Output format</div>
            <label>Convert to</label>
            <select name="target_format" id="target-format">
              <optgroup label="Audio">
                <option value="mp3">MP3</option>
                <option value="wav">WAV</option>
                <option value="m4a">M4A</option>
                <option value="ogg">OGG</option>
                <option value="flac">FLAC</option>
                <option value="aac">AAC</option>
              </optgroup>
              <optgroup label="Video">
                <option value="mp4">MP4</option>
                <option value="webm">WEBM</option>
                <option value="mov">MOV</option>
              </optgroup>
              <optgroup label="Image">
                <option value="jpg">JPG</option>
                <option value="png">PNG</option>
                <option value="webp">WEBP</option>
                <option value="gif">GIF</option>
                <option value="bmp">BMP</option>
              </optgroup>
              <optgroup label="Document">
                <option value="docx">DOCX (Word)</option>
                <option value="pptx">PPTX (PowerPoint)</option>
                <option value="txt">TXT</option>
                <option value="xlsx">XLSX (Excel)</option>
                <option value="csv">CSV</option>
              </optgroup>
            </select>
            <p class="hint">Converting a video to an audio-only format (MP3, WAV, etc.) extracts just the audio track. Document conversion (DOCX/PPTX/XLSX/CSV/PDF) moves the <b>text content only</b> -- design, fonts, and slide layouts aren't preserved.</p>

            <button type="button" id="transcribe-btn" class="primary-btn play">Convert</button>
            <div id="status"></div>
            <div id="js-check" style="display:none;"></div>
          </div>
        </div>

        <div class="output-col">
          <div class="empty-state" id="empty-state">
            <svg class="empty-stamp" viewBox="0 0 40 40">
              <circle cx="20" cy="20" r="17" fill="none" stroke="#e8a33d" stroke-width="2.2" stroke-dasharray="2.6 3.4"/>
              <circle cx="20" cy="20" r="11" fill="#e8a33d"/>
              <path d="M14 20.5l4 4 8-8.5" stroke="#241a08" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <strong>Your converted file will appear here</strong>
            <span>Choose a file and a target format, then convert.</span>
          </div>

          <div id="result" class="simple-panel">
            <div class="player-header">
              <h3 id="result-title">Converted</h3>
            </div>
            <div class="player-row">
              <audio id="preview-audio" controls style="display:none;"></audio>
              <video id="preview-video" controls style="display:none; max-width: 100%; border-radius: 8px;"></video>
              <img id="preview-image" style="display:none; max-width: 100%; max-height: 320px; border-radius: 8px;">
              <a id="dl-link" class="dl-link" download>Download file</a>
            </div>
          </div>
        </div>
      </div>
    </form>

    <div class="features">
      <div class="feature"><strong>Audio formats</strong>MP3, WAV, M4A, OGG, FLAC, AAC.</div>
      <div class="feature"><strong>Video formats</strong>MP4, WEBM, MOV.</div>
      <div class="feature"><strong>Image formats</strong>JPG, PNG, WEBP, GIF, BMP.</div>
      <div class="feature"><strong>Documents</strong>DOCX &harr; PPTX, XLSX &harr; CSV, PDF &rarr; TXT/DOCX/PPTX (text only).</div>
    </div>
  </div>

<script src="/static/pwa.js"></script>
<script src="/static/convert.js"></script>
</body>
</html>
"""


@app.route("/convert")
def convert_page():
    return render_template_string(CONVERT_PAGE, user_email=session.get("user_email"))


SUPPORTED_INPUT_EXTENSIONS = {
    "mp3", "wav", "m4a", "ogg", "flac", "aac", "wma", "opus",
    "mp4", "webm", "mov", "mkv", "avi", "m4v",
    "jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "tif",
    "docx", "pptx", "xlsx", "csv", "pdf",
}


@app.route("/api/convert", methods=["POST"])
@api_login_required
def convert_api():
    if "file" not in request.files:
        return jsonify({"error": "No file received"}), 400

    upload = request.files["file"]
    target_format = request.form.get("target_format", "mp3").strip().lower()
    if target_format not in ALL_CONVERT_FORMATS:
        return jsonify({"error": "Unsupported target format"}), 400

    input_ext = os.path.splitext(upload.filename)[1].lstrip(".").lower()
    if input_ext not in SUPPORTED_INPUT_EXTENSIONS:
        return jsonify({
            "error": f"\".{input_ext}\" isn't a supported input type here. "
                     f"Supported: audio, video, image, DOCX, PPTX, XLSX, CSV, PDF."
        }), 400

    tmp_dir = tempfile.mkdtemp()
    input_path = os.path.join(tmp_dir, upload.filename)
    upload.save(input_path)

    base_name = os.path.splitext(upload.filename)[0] or "converted"
    output_filename = f"{base_name}.{target_format}"
    output_path = os.path.join(tmp_dir, output_filename)

    try:
        convert_file(input_path, output_path, target_format)
    except Exception as e:
        return jsonify({"error": f"Conversion failed: {e}"}), 500

    if not os.path.exists(output_path):
        return jsonify({"error": "Conversion did not produce an output file."}), 500

    job_id = str(uuid.uuid4())
    CONVERT_JOBS[job_id] = output_path
    return jsonify({"job_id": job_id, "filename": output_filename, "format": target_format})


@app.route("/api/convert/download/<job_id>")
@api_login_required
def convert_download(job_id):
    path = CONVERT_JOBS.get(job_id)
    if not path or not os.path.exists(path):
        return "File not found or expired.", 404
    return send_file(path, as_attachment=True)


IMAGE_SIZE_PRESETS = {
    "square": (1024, 1024),
    "landscape": (1344, 768),
    "portrait": (768, 1344),
    "story": (1080, 1920),
}


def generate_one_image(prompt, width, height, model, seed):
    """
    Fetch one image from Pollinations.ai -- a free, no-API-key image
    generation service. Retries a couple of times with backoff if the
    anonymous tier's rate limit (429, or a too-small placeholder image
    returned with a 200) is hit.
    """
    import io
    import time
    import urllib.parse

    from PIL import Image as PILImage

    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}"
    params = {
        "width": width,
        "height": height,
        "model": model,
        "seed": seed,
        "nologo": "true",
    }

    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=90)
            if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("image/"):
                # The rate-limited/anonymous-tier response can still come
                # back as a 200 with an image content-type but be a tiny
                # placeholder rather than the real generated picture --
                # verify it actually decodes and is close to the size we
                # asked for before trusting it.
                try:
                    img = PILImage.open(io.BytesIO(resp.content))
                    img.verify()
                    img2 = PILImage.open(io.BytesIO(resp.content))
                    if img2.width >= width * 0.5 and img2.height >= height * 0.5:
                        return resp.content
                    last_error = "Received an unexpectedly small placeholder image (likely rate-limited)."
                except Exception:
                    last_error = "Received a response that wasn't a valid image (likely rate-limited)."
            elif resp.status_code == 429:
                last_error = "Rate limited by the free image service."
            else:
                last_error = f"Image service returned status {resp.status_code}."
        except requests.RequestException as e:
            last_error = str(e)
        # Back off before retrying -- the anonymous tier allows roughly
        # one request per 15 seconds.
        time.sleep(16)

    raise RuntimeError(last_error or "Could not generate this image.")


VOICES = [
    ("US English", [
        ("en-US-AriaNeural", "Aria (female)"),
        ("en-US-JennyNeural", "Jenny (female, conversational)"),
        ("en-US-MichelleNeural", "Michelle (female)"),
        ("en-US-AmberNeural", "Amber (female)"),
        ("en-US-AnaNeural", "Ana (female, young)"),
        ("en-US-AshleyNeural", "Ashley (female)"),
        ("en-US-CoraNeural", "Cora (female)"),
        ("en-US-ElizabethNeural", "Elizabeth (female)"),
        ("en-US-MonicaNeural", "Monica (female)"),
        ("en-US-SaraNeural", "Sara (female)"),
        ("en-US-GuyNeural", "Guy (male)"),
        ("en-US-DavisNeural", "Davis (male)"),
        ("en-US-EricNeural", "Eric (male)"),
        ("en-US-RogerNeural", "Roger (male)"),
        ("en-US-ChristopherNeural", "Christopher (male)"),
        ("en-US-BrandonNeural", "Brandon (male)"),
        ("en-US-JacobNeural", "Jacob (male)"),
        ("en-US-JasonNeural", "Jason (male)"),
        ("en-US-TonyNeural", "Tony (male)"),
        ("en-US-SteffanNeural", "Steffan (male, deep)"),
    ]),
    ("UK English", [
        ("en-GB-SoniaNeural", "Sonia (female)"),
        ("en-GB-LibbyNeural", "Libby (female)"),
        ("en-GB-MaisieNeural", "Maisie (female, young)"),
        ("en-GB-RyanNeural", "Ryan (male)"),
        ("en-GB-ThomasNeural", "Thomas (male)"),
    ]),
    ("Australian English", [
        ("en-AU-NatashaNeural", "Natasha (female)"),
        ("en-AU-WilliamNeural", "William (male)"),
    ]),
    ("Canadian / Indian / Other English", [
        ("en-CA-ClaraNeural", "Clara (female, Canadian)"),
        ("en-CA-LiamNeural", "Liam (male, Canadian)"),
        ("en-IN-NeerjaNeural", "Neerja (female, Indian)"),
        ("en-IN-PrabhatNeural", "Prabhat (male, Indian)"),
        ("en-IE-EmilyNeural", "Emily (female, Irish)"),
        ("en-IE-ConnorNeural", "Connor (male, Irish)"),
        ("en-ZA-LeahNeural", "Leah (female, South African)"),
        ("en-ZA-LukeNeural", "Luke (male, South African)"),
        ("en-NZ-MollyNeural", "Molly (female, New Zealand)"),
        ("en-NZ-MitchellNeural", "Mitchell (male, New Zealand)"),
    ]),
    ("Urdu", [
        ("ur-PK-UzmaNeural", "Uzma (female)"),
        ("ur-PK-AsadNeural", "Asad (male)"),
        ("ur-IN-GulNeural", "Gul (female, India)"),
        ("ur-IN-SalmanNeural", "Salman (male, India)"),
    ]),
    ("Hindi", [
        ("hi-IN-SwaraNeural", "Swara (female)"),
        ("hi-IN-MadhurNeural", "Madhur (male)"),
    ]),
    ("Arabic", [
        ("ar-SA-ZariyahNeural", "Zariyah (female, Saudi)"),
        ("ar-SA-HamedNeural", "Hamed (male, Saudi)"),
        ("ar-AE-FatimaNeural", "Fatima (female, UAE)"),
        ("ar-AE-HamdanNeural", "Hamdan (male, UAE)"),
        ("ar-EG-SalmaNeural", "Salma (female, Egypt)"),
        ("ar-EG-ShakirNeural", "Shakir (male, Egypt)"),
    ]),
    ("French", [
        ("fr-FR-DeniseNeural", "Denise (female)"),
        ("fr-FR-EloiseNeural", "Eloise (female, young)"),
        ("fr-FR-HenriNeural", "Henri (male)"),
        ("fr-CA-SylvieNeural", "Sylvie (female, Canadian)"),
        ("fr-CA-JeanNeural", "Jean (male, Canadian)"),
    ]),
    ("Spanish", [
        ("es-ES-ElviraNeural", "Elvira (female, Spain)"),
        ("es-ES-AlvaroNeural", "Alvaro (male, Spain)"),
        ("es-MX-DaliaNeural", "Dalia (female, Mexico)"),
        ("es-MX-JorgeNeural", "Jorge (male, Mexico)"),
        ("es-AR-ElenaNeural", "Elena (female, Argentina)"),
        ("es-AR-TomasNeural", "Tomas (male, Argentina)"),
        ("es-US-PalomaNeural", "Paloma (female, US Spanish)"),
        ("es-US-AlonsoNeural", "Alonso (male, US Spanish)"),
    ]),
    ("German", [
        ("de-DE-KatjaNeural", "Katja (female)"),
        ("de-DE-AmalaNeural", "Amala (female)"),
        ("de-DE-ConradNeural", "Conrad (male)"),
        ("de-DE-KillianNeural", "Killian (male)"),
        ("de-AT-IngridNeural", "Ingrid (female, Austrian)"),
        ("de-CH-LeniNeural", "Leni (female, Swiss)"),
    ]),
    ("Italian", [
        ("it-IT-ElsaNeural", "Elsa (female)"),
        ("it-IT-IsabellaNeural", "Isabella (female)"),
        ("it-IT-DiegoNeural", "Diego (male)"),
    ]),
    ("Portuguese", [
        ("pt-BR-FranciscaNeural", "Francisca (female, Brazil)"),
        ("pt-BR-AntonioNeural", "Antonio (male, Brazil)"),
        ("pt-PT-RaquelNeural", "Raquel (female, Portugal)"),
        ("pt-PT-DuarteNeural", "Duarte (male, Portugal)"),
    ]),
    ("Russian", [
        ("ru-RU-SvetlanaNeural", "Svetlana (female)"),
        ("ru-RU-DmitryNeural", "Dmitry (male)"),
    ]),
    ("Chinese", [
        ("zh-CN-XiaoxiaoNeural", "Xiaoxiao (female, Mandarin)"),
        ("zh-CN-XiaoyiNeural", "Xiaoyi (female, Mandarin)"),
        ("zh-CN-YunxiNeural", "Yunxi (male, Mandarin)"),
        ("zh-CN-YunjianNeural", "Yunjian (male, Mandarin)"),
        ("zh-HK-HiuMaanNeural", "HiuMaan (female, Cantonese)"),
        ("zh-TW-HsiaoChenNeural", "HsiaoChen (female, Taiwan)"),
    ]),
    ("Japanese", [
        ("ja-JP-NanamiNeural", "Nanami (female)"),
        ("ja-JP-KeitaNeural", "Keita (male)"),
    ]),
    ("Korean", [
        ("ko-KR-SunHiNeural", "SunHi (female)"),
        ("ko-KR-InJoonNeural", "InJoon (male)"),
    ]),
    ("Turkish", [
        ("tr-TR-EmelNeural", "Emel (female)"),
        ("tr-TR-AhmetNeural", "Ahmet (male)"),
    ]),
]

PREVIEW_TEXTS = {
    "en": "Hello, this is a preview of this voice.",
    "ur": "\u06c1\u06cc\u0644\u0648\u060c \u06cc\u06c1 \u0645\u06cc\u0631\u06cc \u0622\u0648\u0627\u0632 \u06a9\u0627 \u0627\u06cc\u06a9 \u0646\u0645\u0648\u0646\u06c1 \u06c1\u06d2\u06d4",
    "hi": "\u0928\u092e\u0938\u094d\u0924\u0947, \u092f\u0939 \u092e\u0947\u0930\u0940 \u0906\u0935\u093e\u091c\u093c \u0915\u093e \u090f\u0915 \u0928\u092e\u0942\u0928\u093e \u0939\u0948\u0964",
    "ar": "\u0645\u0631\u062d\u0628\u064b\u0627\u060c \u0647\u0630\u0647 \u0639\u064a\u0646\u0629 \u0645\u0646 \u0635\u0648\u062a\u064a.",
    "fr": "Bonjour, ceci est un aper\u00e7u de cette voix.",
    "es": "Hola, esta es una muestra de esta voz.",
    "de": "Hallo, dies ist eine H\u00f6rprobe dieser Stimme.",
    "zh": "\u4f60\u597d\uff0c\u8fd9\u662f\u8fd9\u4e2a\u58f0\u97f3\u7684\u9884\u89c8\u3002",
    "ja": "\u3053\u3093\u306b\u3061\u306f\u3001\u3053\u308c\u306f\u3053\u306e\u58f0\u306e\u30d7\u30ec\u30d3\u30e5\u30fc\u3067\u3059\u3002",
    "tr": "Merhaba, bu bu sesin bir \u00f6rne\u011fi.",
    "it": "Ciao, questa \u00e8 un'anteprima di questa voce.",
    "pt": "Ol\u00e1, esta \u00e9 uma pr\u00e9via desta voz.",
    "ru": "\u041f\u0440\u0438\u0432\u0435\u0442, \u044d\u0442\u043e \u043f\u0440\u0435\u0434\u0432\u0430\u0440\u0438\u0442\u0435\u043b\u044c\u043d\u043e\u0435 \u043f\u0440\u043e\u0441\u043b\u0443\u0448\u0438\u0432\u0430\u043d\u0438\u0435 \u044d\u0442\u043e\u0433\u043e \u0433\u043e\u043b\u043e\u0441\u0430.",
    "ko": "\uc548\ub155\ud558\uc138\uc694, \uc774\uac83\uc740 \uc774 \ubaa9\uc18c\ub9ac\uc758 \ubbf8\ub9ac\ubcf4\uae30\uc785\ub2c8\ub2e4.",
}


def all_voice_values():
    return {v for _, voices in VOICES for v, _ in voices}


def generate_voiceover(text, voice, rate_percent, output_path):
    """Generate speech with edge-tts (Microsoft's free neural voices)."""
    import asyncio
    import edge_tts

    rate_str = f"{'+' if rate_percent >= 0 else ''}{rate_percent}%"

    async def _run():
        communicate = edge_tts.Communicate(text, voice, rate=rate_str)
        await communicate.save(output_path)

    asyncio.run(_run())


VOICEOVER_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoxStamp -- Voiceover</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Ccircle cx='20' cy='20' r='17' fill='%23e8a33d'/%3E%3Cpath d='M11 21l5 5 13-13' stroke='%23241a08' stroke-width='3.4' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#0f1115">
<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <div class="topbar">
    <div class="topbar-inner">
      <div class="stamp">
        <svg viewBox="0 0 40 40">
          <circle class="ring" cx="20" cy="20" r="17"/>
          <circle cx="20" cy="20" r="11" fill="var(--amber)"/>
          <path d="M14 20.5l4 4 8-8.5" stroke="var(--amber-ink)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="brand">VoxStamp</div>
      <div class="nav-tabs">
        <a href="/">Transcribe</a>
        <a href="/convert">Convert</a>
        <a href="/voiceover" class="active">Voiceover</a>
        <a href="/images">Images</a>
      </div>
      <div class="topbar-user">{% if user_email %}{{ user_email }} &middot; <a href="/logout">Log out</a>{% else %}<a href="/login">Log in</a>{% endif %}</div>
    </div>
  </div>

  <div class="wrap">
    <div class="install-banner" id="install-banner">
      <div class="install-banner-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#241a08" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 16V4M12 4l-4 4M12 4l4 4"/><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"/></svg>
      </div>
      <div class="install-banner-text">
        <strong>Install VoxStamp as an app</strong>
        <span>Opens in its own window, works offline for the app shell, and sits on your desktop or home screen.</span>
      </div>
      <button id="install-app-btn" type="button">Install</button>
    </div>
    <div class="intro">
      <h1>Generate a voiceover</h1>
      <p>Type or paste a script and get natural-sounding narration back as an MP3 -- free neural voices, no per-character cost.</p>
    </div>

    <form id="f">
      <div class="layout">
        <div class="setup-col">
          <div class="panel">
            <div class="section-title">Script</div>
            <label>Text to speak</label>
            <textarea name="text" id="script-text" rows="8" placeholder="Paste or write the script you want narrated..." required></textarea>
            <p class="hint" id="char-count">0 characters</p>

            <div class="section-title">Voice</div>
            <label>Narrator</label>
            <select name="voice" id="voice-select">
              __VOICE_OPTIONS__
            </select>
            <button type="button" id="preview-voice-btn" class="primary-btn" style="margin-top:8px; background: var(--panel-alt); color: var(--text); border: 1px solid var(--border);">&#9658; Preview this voice</button>
            <audio id="preview-voice-audio" style="display:none;"></audio>
            <label>Speed</label>
            <input type="range" name="rate" id="rate-slider" min="-50" max="50" step="5" value="0">
            <p class="hint" id="rate-label">Normal speed</p>

            <button type="button" id="transcribe-btn" class="primary-btn play">Generate voiceover</button>
            <div id="status"></div>
            <div id="js-check" style="display:none;"></div>
          </div>
        </div>

        <div class="output-col">
          <div class="empty-state" id="empty-state">
            <svg class="empty-stamp" viewBox="0 0 40 40">
              <circle cx="20" cy="20" r="17" fill="none" stroke="#e8a33d" stroke-width="2.2" stroke-dasharray="2.6 3.4"/>
              <circle cx="20" cy="20" r="11" fill="#e8a33d"/>
              <path d="M14 20.5l4 4 8-8.5" stroke="#241a08" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <strong>Your voiceover will appear here</strong>
            <span>Write a script, pick a voice, and generate -- you'll get a playable, downloadable MP3.</span>
          </div>

          <div id="result" class="simple-panel">
            <div class="player-header">
              <h3 id="result-title">Voiceover</h3>
            </div>
            <div class="player-row">
              <audio id="preview-audio" controls></audio>
              <a id="dl-link" class="dl-link" download>Download MP3</a>
            </div>
          </div>
        </div>
      </div>
    </form>

    <div class="features">
      <div class="feature"><strong>Free neural voices</strong>Microsoft's studio-quality voices, no per-character billing.</div>
      <div class="feature"><strong>Multiple languages</strong>English (US/UK/AU), Urdu, Hindi, Arabic and more.</div>
      <div class="feature"><strong>Adjustable speed</strong>Slow it down or speed it up to match your video's pacing.</div>
      <div class="feature"><strong>Instant MP3</strong>Download and drop straight into your edit.</div>
    </div>
  </div>

<script src="/static/pwa.js"></script>
<script src="/static/voiceover.js"></script>
</body>
</html>
"""


def _voiceover_page_html():
    groups = []
    for group_label, voices in VOICES:
        opts = "\n".join(f'<option value="{v}">{label}</option>' for v, label in voices)
        groups.append(f'<optgroup label="{group_label}">\n{opts}\n</optgroup>')
    return VOICEOVER_PAGE.replace("__VOICE_OPTIONS__", "\n".join(groups))


@app.route("/voiceover")
def voiceover_page():
    return render_template_string(_voiceover_page_html(), user_email=session.get("user_email"))


@app.route("/api/voiceover", methods=["POST"])
@api_login_required
def voiceover_api():
    text = request.form.get("text", "").strip()
    voice = request.form.get("voice", "en-US-AriaNeural")
    try:
        rate_percent = int(request.form.get("rate", 0))
    except ValueError:
        rate_percent = 0

    if not text:
        return jsonify({"error": "Enter some text to narrate."}), 400
    if len(text) > 20000:
        return jsonify({"error": "Text is too long (max 20,000 characters)."}), 400
    if voice not in all_voice_values():
        return jsonify({"error": "Unknown voice."}), 400

    tmp_dir = tempfile.mkdtemp()
    output_path = os.path.join(tmp_dir, "voiceover.mp3")

    try:
        generate_voiceover(text, voice, rate_percent, output_path)
    except Exception as e:
        return jsonify({"error": f"Voiceover generation failed: {e}"}), 500

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return jsonify({"error": "No audio was produced."}), 500

    job_id = str(uuid.uuid4())
    VOICEOVER_JOBS[job_id] = output_path
    return jsonify({"job_id": job_id, "filename": "voiceover.mp3"})


@app.route("/api/voiceover/download/<job_id>")
@api_login_required
def voiceover_download(job_id):
    path = VOICEOVER_JOBS.get(job_id)
    if not path or not os.path.exists(path):
        return "File not found or expired.", 404
    return send_file(path, as_attachment=True)


@app.route("/api/voice-preview", methods=["POST"])
@api_login_required
def voice_preview_api():
    voice = request.form.get("voice", "")
    if voice not in all_voice_values():
        return jsonify({"error": "Unknown voice."}), 400

    lang_prefix = voice.split("-")[0]
    preview_text = PREVIEW_TEXTS.get(lang_prefix, PREVIEW_TEXTS["en"])

    tmp_dir = tempfile.mkdtemp()
    output_path = os.path.join(tmp_dir, "preview.mp3")

    try:
        generate_voiceover(preview_text, voice, 0, output_path)
    except Exception as e:
        return jsonify({"error": f"Preview failed: {e}"}), 500

    job_id = str(uuid.uuid4())
    VOICEOVER_JOBS[job_id] = output_path
    return jsonify({"job_id": job_id})


IMAGES_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VoxStamp -- Images</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40'%3E%3Ccircle cx='20' cy='20' r='17' fill='%23e8a33d'/%3E%3Cpath d='M11 21l5 5 13-13' stroke='%23241a08' stroke-width='3.4' fill='none' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E">
<link rel="manifest" href="/static/manifest.json">
<meta name="theme-color" content="#0f1115">
<link rel="apple-touch-icon" href="/static/icons/apple-touch-icon.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="/static/style.css">
<style>
  .image-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
  .image-card { border: 1px solid var(--border); border-radius: 12px; overflow: hidden; background: rgba(255,255,255,.03); }
  .image-card img { width: 100%; height: 150px; object-fit: cover; display: block; background: rgba(255,255,255,.04); }
  .image-card-body { padding: 8px 10px; }
  .image-card-prompt { font-size: 11px; color: var(--muted); line-height: 1.4; max-height: 2.8em; overflow: hidden; }
  .image-card-actions { display: flex; gap: 6px; margin-top: 6px; }
  .image-card-actions a, .image-card-actions span {
    font-size: 11px; font-family: 'Space Grotesk', sans-serif; font-weight: 600;
    color: var(--amber-hi); text-decoration: none;
  }
  .image-card.failed { opacity: .55; }
  .image-card.failed .image-card-prompt { color: #e87a5d; }
</style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-inner">
      <div class="stamp">
        <svg viewBox="0 0 40 40">
          <circle class="ring" cx="20" cy="20" r="17"/>
          <circle cx="20" cy="20" r="11" fill="var(--amber)"/>
          <path d="M14 20.5l4 4 8-8.5" stroke="var(--amber-ink)" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="brand">VoxStamp</div>
      <div class="nav-tabs">
        <a href="/">Transcribe</a>
        <a href="/convert">Convert</a>
        <a href="/voiceover">Voiceover</a>
        <a href="/images" class="active">Images</a>
      </div>
      <div class="topbar-user">{{ user_email }} &middot; <a href="/logout">Log out</a></div>
    </div>
  </div>

  <div class="wrap">
    <div class="intro">
      <h1>Generate images in bulk</h1>
      <p>One prompt per line -- generate a whole batch at once, preview each result, then download them individually or as a ZIP. Uses a free image service, no API key needed.</p>
    </div>

    <form id="f">
      <div class="layout">
        <div class="setup-col">
          <div class="panel">
            <div class="section-title">Prompts</div>
            <label>One prompt per line</label>
            <textarea name="prompts" id="prompts-text" rows="9" placeholder="a golden retriever puppy in a sunlit garden&#10;a minimalist logo of a mountain, flat design&#10;a cozy coffee shop interior, warm lighting" required></textarea>
            <p class="hint" id="prompt-count">0 prompts</p>

            <div class="section-title">Options</div>
            <label>Aspect ratio</label>
            <select name="size_preset" id="size-preset">
              <option value="square">Square (1024x1024)</option>
              <option value="landscape">Landscape (1344x768)</option>
              <option value="portrait">Portrait (768x1344)</option>
              <option value="story">Story / Reel (1080x1920)</option>
            </select>
            <label>Style</label>
            <select name="model" id="model-select">
              <option value="flux">Flux (general purpose, sharp)</option>
              <option value="turbo">Turbo (faster, looser)</option>
            </select>
            <p class="hint">The free tier allows about 1 image every 15-20 seconds, so a batch of 10 takes roughly 3-5 minutes. This pacing is deliberate -- it's what keeps every image correct instead of a rushed, wrong result.</p>

            <button type="button" id="transcribe-btn" class="primary-btn play">Generate batch</button>
            <div id="status"></div>
            <div id="js-check" style="display:none;"></div>
          </div>
        </div>

        <div class="output-col">
          <div class="empty-state" id="empty-state">
            <svg class="empty-stamp" viewBox="0 0 40 40">
              <circle cx="20" cy="20" r="17" fill="none" stroke="#e8a33d" stroke-width="2.2" stroke-dasharray="2.6 3.4"/>
              <circle cx="20" cy="20" r="11" fill="#e8a33d"/>
              <path d="M14 20.5l4 4 8-8.5" stroke="#241a08" stroke-width="2.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
            <strong>Your images will appear here</strong>
            <span>Write one or more prompts, one per line, and generate.</span>
          </div>

          <div id="result">
            <div class="player-header">
              <h3>Results <span id="line-count"></span></h3>
              <div class="format-chips">
                <a id="dl-zip" class="dl-link" download>Download all (.zip)</a>
              </div>
            </div>
            <div class="image-grid" id="image-grid"></div>
          </div>
        </div>
      </div>
    </form>

    <div class="features">
      <div class="feature"><strong>Bulk generation</strong>Paste dozens of prompts, get them all in one batch.</div>
      <div class="feature"><strong>No API key</strong>Uses a free, open image service -- nothing to sign up for.</div>
      <div class="feature"><strong>Multiple aspect ratios</strong>Square, landscape, portrait, and story/reel sizes.</div>
      <div class="feature"><strong>ZIP export</strong>Download the whole batch in one file.</div>
    </div>
  </div>

<script src="/static/pwa.js"></script>
<script src="/static/images.js"></script>
</body>
</html>
"""


@app.route("/images")
def images_page():
    return render_template_string(IMAGES_PAGE, user_email=session.get("user_email"))


@app.route("/api/generate-images", methods=["POST"])
@api_login_required
def generate_images_api():
    import random
    import time
    import zipfile

    prompts_raw = request.form.get("prompts", "")
    prompts = [p.strip() for p in prompts_raw.split("\n") if p.strip()]
    if not prompts:
        return jsonify({"error": "Enter at least one prompt."}), 400
    if len(prompts) > 30:
        return jsonify({"error": "Max 30 prompts per batch -- split larger batches into a few runs."}), 400

    size_preset = request.form.get("size_preset", "square")
    width, height = IMAGE_SIZE_PRESETS.get(size_preset, IMAGE_SIZE_PRESETS["square"])
    model = request.form.get("model", "flux")
    if model not in ("flux", "turbo"):
        model = "flux"

    tmp_dir = tempfile.mkdtemp()
    results = []
    for i, prompt in enumerate(prompts):
        if i > 0:
            # Anonymous tier allows roughly 1 request/15s -- space batch
            # requests out so later prompts don't silently get a bad
            # (rate-limited) result.
            time.sleep(16)
        seed = random.randint(1, 2_000_000_000)
        entry = {"prompt": prompt, "ok": False, "filename": None, "error": None}
        try:
            image_bytes = generate_one_image(prompt, width, height, model, seed)
            filename = f"{i + 1:02d}.jpg"
            with open(os.path.join(tmp_dir, filename), "wb") as f:
                f.write(image_bytes)
            entry["ok"] = True
            entry["filename"] = filename
        except Exception as e:
            entry["error"] = str(e)
        results.append(entry)

    zip_path = os.path.join(tmp_dir, "images.zip")
    with zipfile.ZipFile(zip_path, "w") as zf:
        for entry in results:
            if entry["ok"]:
                zf.write(os.path.join(tmp_dir, entry["filename"]), entry["filename"])

    job_id = str(uuid.uuid4())
    IMAGE_JOBS[job_id] = tmp_dir

    ok_count = sum(1 for r in results if r["ok"])
    return jsonify({
        "job_id": job_id,
        "results": results,
        "ok_count": ok_count,
        "total": len(results),
    })


@app.route("/api/images/download/<job_id>/<filename>")
@api_login_required
def image_download(job_id, filename):
    tmp_dir = IMAGE_JOBS.get(job_id)
    if not tmp_dir:
        return "Batch not found or expired.", 404
    path = os.path.join(tmp_dir, filename)
    if not os.path.exists(path):
        return "File not found.", 404
    as_attachment = filename.endswith(".zip")
    return send_file(path, as_attachment=as_attachment)


AUTH_PAGE = """
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} -- VoxStamp</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #0a0b0f; --panel: rgba(255,255,255,0.035); --panel-alt: rgba(255,255,255,0.05); --border: rgba(255,255,255,0.09);
    --text: #f4f2ec; --muted: #8d90a0; --amber: #eda94a; --amber-hi: #ffc36b; --amber-dim: #b9812f; --amber-ink: #201404;
    --error: #e87a5d;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Inter', system-ui, sans-serif; margin: 0; min-height: 100vh;
    color: var(--text);
    background-color: var(--bg);
    background-image:
      radial-gradient(900px 500px at 15% -10%, rgba(237,169,74,.16), transparent 60%),
      radial-gradient(700px 480px at 90% 10%, rgba(99,130,220,.10), transparent 55%);
    display: flex; align-items: center; justify-content: center; padding: 20px;
  }}
  .waveform {{ display: flex; align-items: center; justify-content: center; gap: 3px; height: 30px; margin-bottom: 14px; }}
  .waveform span {{
    display: block; width: 3px; border-radius: 2px;
    background: linear-gradient(180deg, var(--amber-hi), var(--amber-dim));
    animation: pulse 1.4s ease-in-out infinite;
  }}
  .waveform span:nth-child(1) {{ height: 10px; animation-delay: 0s; }}
  .waveform span:nth-child(2) {{ height: 20px; animation-delay: .12s; }}
  .waveform span:nth-child(3) {{ height: 30px; animation-delay: .24s; }}
  .waveform span:nth-child(4) {{ height: 16px; animation-delay: .36s; }}
  .waveform span:nth-child(5) {{ height: 24px; animation-delay: .48s; }}
  .waveform span:nth-child(6) {{ height: 12px; animation-delay: .6s; }}
  @keyframes pulse {{ 0%, 100% {{ transform: scaleY(0.55); opacity: .75; }} 50% {{ transform: scaleY(1); opacity: 1; }} }}
  @media (prefers-reduced-motion: reduce) {{ .waveform span {{ animation: none; transform: scaleY(0.8); }} }}
  .card {{
    width: 100%; max-width: 380px;
    background: linear-gradient(165deg, var(--panel-alt), var(--panel));
    border: 1px solid var(--border);
    backdrop-filter: blur(16px);
    border-radius: 20px; padding: 34px 28px; text-align: center;
    box-shadow: 0 24px 60px -20px rgba(0,0,0,.65);
  }}
  h1 {{ font-family: 'Space Grotesk', sans-serif; font-size: 20px; margin: 0 0 4px; }}
  .sub {{ color: var(--muted); font-size: 13.5px; margin: 0 0 24px; }}
  label {{ display: block; text-align: left; font-size: 13px; color: var(--muted); font-weight: 500; margin-top: 14px; }}
  label:first-of-type {{ margin-top: 0; }}
  input {{
    width: 100%; padding: 11px 12px; margin-top: 6px; border-radius: 9px;
    border: 1px solid var(--border); background: rgba(0,0,0,.22); color: var(--text);
    font-family: inherit; font-size: 14.5px; transition: border-color .15s, box-shadow .15s;
  }}
  input:focus {{ outline: none; border-color: var(--amber); box-shadow: 0 0 0 3px rgba(237,169,74,.18); }}
  button {{
    width: 100%; margin-top: 20px; padding: 12px; border: none; border-radius: 10px;
    background: linear-gradient(155deg, var(--amber-hi), var(--amber) 60%, var(--amber-dim));
    color: var(--amber-ink); font-weight: 600; font-size: 14.5px;
    cursor: pointer; font-family: 'Space Grotesk', sans-serif;
    box-shadow: 0 8px 30px -8px rgba(237,169,74,.4);
    transition: transform .15s, box-shadow .15s;
  }}
  button:hover {{ transform: translateY(-1px); box-shadow: 0 14px 36px -8px rgba(237,169,74,.55); }}
  .switch {{ margin-top: 18px; font-size: 13px; color: var(--muted); }}
  .switch a {{ color: var(--amber-hi); text-decoration: none; font-weight: 600; }}
  .error {{ background: rgba(232,122,93,.12); border: 1px solid var(--error); color: var(--error);
    border-radius: 9px; padding: 9px 12px; font-size: 13px; margin-top: 16px; text-align: left; }}
  .google-btn {{
    width: 100%; margin-top: 0; padding: 11px; border-radius: 10px;
    background: rgba(255,255,255,.04); color: var(--text); border: 1px solid var(--border);
    font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 500; cursor: pointer;
    display: flex; align-items: center; justify-content: center; gap: 10px; text-decoration: none;
    transition: border-color .15s, background .15s;
  }}
  .google-btn:hover {{ border-color: var(--amber); background: rgba(237,169,74,.06); }}
  .divider {{ display: flex; align-items: center; gap: 10px; margin: 18px 0; color: var(--muted); font-size: 12px; }}
  .divider::before, .divider::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}
</style>
</head>
<body>
  <div>
    <div class="waveform"><span></span><span></span><span></span><span></span><span></span><span></span></div>
    <div class="card">
      <h1>{heading}</h1>
      <p class="sub">{subheading}</p>
      {google_html}
      <form method="post">
        <label>Email</label>
        <input type="email" name="email" required autofocus value="{email_value}">
        <label>Password</label>
        <input type="password" name="password" required minlength="6">
        {error_html}
        <button type="submit">{button_text}</button>
      </form>
      <div class="switch">{switch_html}</div>
    </div>
  </div>
</body>
</html>
"""

GOOGLE_BUTTON_HTML = """
      <a href="/login/google" class="google-btn">
        <svg width="18" height="18" viewBox="0 0 18 18"><path fill="#4285F4" d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.9c1.7-1.57 2.7-3.88 2.7-6.62z"/><path fill="#34A853" d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.9-2.26c-.8.54-1.84.86-3.06.86-2.35 0-4.34-1.59-5.05-3.72H.96v2.33A9 9 0 0 0 9 18z"/><path fill="#FBBC05" d="M3.95 10.7A5.4 5.4 0 0 1 3.67 9c0-.59.1-1.17.28-1.7V4.97H.96A9 9 0 0 0 0 9c0 1.45.35 2.83.96 4.03l2.99-2.33z"/><path fill="#EA4335" d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.97l2.99 2.33C4.66 5.17 6.65 3.58 9 3.58z"/></svg>
        Continue with Google
      </a>
      <div class="divider">or</div>
"""


def render_auth_page(title, heading, subheading, button_text, switch_html, error=None, email_value=""):
    error_html = '<div class="error">' + error + '</div>' if error else ''
    google_html = GOOGLE_BUTTON_HTML if GOOGLE_LOGIN_ENABLED else ''
    return AUTH_PAGE.format(
        title=title, heading=heading, subheading=subheading, button_text=button_text,
        switch_html=switch_html, error_html=error_html, email_value=email_value,
        google_html=google_html,
    )


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_auth_page(
            "Sign up", "Create your account", "Sign up to use VoxStamp", "Sign up",
            'Already have an account? <a href="/login">Log in</a>',
        )

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not email or not password or len(password) < 6:
        return render_auth_page(
            "Sign up", "Create your account", "Sign up to use VoxStamp", "Sign up",
            'Already have an account? <a href="/login">Log in</a>',
            error="Enter a valid email and a password of at least 6 characters.",
            email_value=email,
        )

    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        return render_auth_page(
            "Sign up", "Create your account", "Sign up to use VoxStamp", "Sign up",
            'Already have an account? <a href="/login">Log in</a>',
            error="An account with this email already exists.",
            email_value=email,
        )

    db.execute(
        "INSERT INTO users (email, password_hash) VALUES (?, ?)",
        (email, generate_password_hash(password)),
    )
    db.commit()
    session["user_email"] = email
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_auth_page(
            "Log in", "Welcome back", "Log in to use VoxStamp", "Log in",
            'No account yet? <a href="/signup">Sign up</a>',
        )

    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    db = get_db()
    user = db.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user or not check_password_hash(user["password_hash"], password):
        return render_auth_page(
            "Log in", "Welcome back", "Log in to use VoxStamp", "Log in",
            'No account yet? <a href="/signup">Sign up</a>',
            error="Incorrect email or password.",
            email_value=email,
        )

    session["user_email"] = email
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("user_email", None)
    return redirect(url_for("login"))


@app.route("/login/google")
def login_google():
    if not GOOGLE_LOGIN_ENABLED:
        return "Google sign-in isn't configured on this server yet.", 501
    redirect_uri = url_for("auth_google_callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.route("/auth/google/callback")
def auth_google_callback():
    if not GOOGLE_LOGIN_ENABLED:
        return "Google sign-in isn't configured on this server yet.", 501

    token = oauth.google.authorize_access_token()
    user_info = token.get("userinfo")
    if not user_info or not user_info.get("email"):
        return redirect(url_for("login"))

    email = user_info["email"].strip().lower()

    # Record the user (no password needed -- they authenticate via Google).
    db = get_db()
    existing = db.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not existing:
        db.execute("INSERT INTO users (email, password_hash) VALUES (?, NULL)", (email,))
        db.commit()

    session["user_email"] = email
    return redirect(url_for("index"))


@app.route("/")
def index():
    return render_template_string(PAGE, user_email=session.get("user_email"))


@app.route("/transcribe", methods=["POST"])
@api_login_required
def transcribe_route():
    if "audio" not in request.files:
        return jsonify({"error": "No audio file received"}), 400

    audio_file = request.files["audio"]
    max_words = int(request.form.get("max_words", 14))
    pause_threshold = float(request.form.get("pause_threshold", 0.35))
    min_words = int(request.form.get("min_words", 2))
    model_size = request.form.get("model", "medium")
    language = request.form.get("language", "").strip() or None
    vocabulary_hint = request.form.get("vocabulary_hint", "").strip() or None
    isolate_terms = request.form.get("isolate_terms", "").strip() or None

    tmp_dir = tempfile.mkdtemp()
    audio_path = os.path.join(tmp_dir, audio_file.filename)
    audio_file.save(audio_path)

    try:
        lines, detected_lang = transcribe_exact(
            audio_path, model_size, max_words, pause_threshold, min_words, language, vocabulary_hint, isolate_terms
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    if not lines:
        return jsonify({"error": "No speech detected in this file."}), 400

    return jsonify({"lines": lines, "language": detected_lang})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
