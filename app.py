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
import tempfile

from flask import Flask, request, render_template_string, jsonify

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024  # 500 MB max upload

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
<title>Exact Timestamp Transcriber</title>
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; max-width: 780px; margin: 40px auto; padding: 0 16px; background: #0f1115; color: #eee; }
  h1 { font-size: 22px; margin-bottom: 4px; }
  .tagline { color: #999; font-size: 14px; margin-top: 0; }
  label { display: block; margin-top: 14px; font-weight: 600; font-size: 14px; }
  input, select, textarea { width: 100%; padding: 8px; margin-top: 4px; border-radius: 6px; border: 1px solid #444; background: #1a1d24; color: #eee; box-sizing: border-box; font-family: inherit; }
  textarea { resize: vertical; }
  button { padding: 12px 20px; border: none; border-radius: 8px; background: #4f7cff; color: white; font-weight: 600; cursor: pointer; }
  button:disabled { background: #555; cursor: not-allowed; }
  #transcribe-btn { margin-top: 20px; width: 100%; }
  .hint { font-size: 12px; color: #888; margin-top: 6px; }
  #status { margin-top: 16px; font-size: 14px; color: #aaa; }
  #result { margin-top: 24px; display: none; }
  .row { display: flex; gap: 12px; }
  .row > div { flex: 1; }
  .line-row { display: flex; gap: 10px; align-items: flex-start; margin-bottom: 8px; padding: 8px; background: #1a1d24; border-radius: 6px; }
  .line-time { color: #6ab0ff; font-size: 12px; font-family: monospace; white-space: nowrap; padding-top: 8px; min-width: 110px; }
  .line-text { flex: 1; padding: 6px 8px; border-radius: 4px; border: 1px solid #333; background: #0f1115; color: #eee; font-family: inherit; }
  .download-bar { display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }
  .download-bar button { flex: 1; min-width: 120px; background: #2a2f3a; }
  .download-bar button:hover { background: #353b48; }
  #copy-btn { background: #2a2f3a; }
  details { margin-top: 14px; }
  summary { cursor: pointer; font-weight: 600; font-size: 14px; color: #aaa; }
</style>
</head>
<body>
  <h1>Exact Timestamp Transcriber</h1>
  <p class="tagline">Real word-level timestamps -- no estimation. Upload audio, set your own line-breaking rules, edit the result, then download.</p>

  <form id="f">
    <label>Audio / video file</label>
    <input type="file" name="audio" required>

    <div class="row">
      <div>
        <label>Pause threshold (seconds)</label>
        <input type="number" name="pause_threshold" value="0.35" step="0.05" min="0.05">
      </div>
      <div>
        <label>Max words (safety limit)</label>
        <input type="number" name="max_words" value="14" min="2">
      </div>
    </div>
    <div class="row">
      <div>
        <label>Min words per line</label>
        <input type="number" name="min_words" value="2" min="1">
      </div>
      <div>
        <label>Model</label>
        <select name="model">
          <option value="tiny">tiny (fastest)</option>
          <option value="base">base</option>
          <option value="small">small</option>
          <option value="medium" selected>medium (recommended)</option>
          <option value="large-v3">large-v3 (most accurate)</option>
        </select>
      </div>
    </div>
    <p class="hint">
      A line breaks at: <b>sentence/clause end</b> (. , ? ! ; :), or wherever the
      speaker pauses for at least the "Pause threshold". "Max words" is only a
      safety cap for run-on speech with no natural pause. Lower the threshold
      for shorter lines, raise it for longer ones.
    </p>

    <details>
      <summary>Advanced options</summary>
      <label>Language (optional, e.g. en, ur -- blank = auto-detect)</label>
      <input type="text" name="language" placeholder="auto-detect">
      <label>Vocabulary hint (optional)</label>
      <textarea name="vocabulary_hint" rows="2" placeholder="e.g. brand names, technical terms, or people's names that appear in this audio -- improves accuracy for words the model wouldn't otherwise recognize"></textarea>
      <label>Isolate these words/names as their own line (optional, comma-separated)</label>
      <input type="text" name="isolate_terms" placeholder="e.g. lion, polar bear, wolf">
      <p class="hint">Whenever any of these words appear, that word (or phrase) gets its own dedicated line, breaking the surrounding text around it.</p>
    </details>

    <button type="button" id="transcribe-btn">Transcribe</button>
  </form>

  <div id="status"></div>
  <div id="js-check" style="margin-top:8px; font-size:12px; color:#555;">Checking script...</div>

  <div id="result">
    <h3>Transcript <span id="line-count" style="color:#888; font-weight:400; font-size:14px;"></span></h3>
    <div id="lines"></div>
    <div class="download-bar">
      <button id="copy-btn" type="button">Copy all text</button>
      <button id="dl-srt" type="button">Download .SRT</button>
      <button id="dl-vtt" type="button">Download .VTT</button>
      <button id="dl-txt" type="button">Download .TXT</button>
    </div>
  </div>

<script src="/static/app.js"></script>
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/transcribe", methods=["POST"])
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
