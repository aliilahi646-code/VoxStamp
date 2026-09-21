try {

const form = document.getElementById('f');
const status = document.getElementById('status');
const result = document.getElementById('result');
const linesDiv = document.getElementById('lines');
const lineCount = document.getElementById('line-count');
const btn = document.getElementById('transcribe-btn');
const dropzone = document.getElementById('dropzone');
const audioInput = document.getElementById('audio-input');
const dropzoneEmpty = document.getElementById('dropzone-empty');
const dropzoneFilename = document.getElementById('dropzone-filename');
const dropzoneFilenameText = document.getElementById('dropzone-filename-text');
const emptyState = document.getElementById('empty-state');

function showChosenFile(file) {
  if (!file) return;
  dropzoneEmpty.style.display = 'none';
  dropzoneFilename.style.display = 'flex';
  dropzoneFilenameText.textContent = file.name;
}

dropzone.addEventListener('click', function () {
  audioInput.click();
});

audioInput.addEventListener('change', function () {
  if (audioInput.files.length) showChosenFile(audioInput.files[0]);
});

['dragover', 'dragenter'].forEach(function (evt) {
  dropzone.addEventListener(evt, function (e) {
    e.preventDefault();
    dropzone.classList.add('dragover');
  });
});

['dragleave', 'dragend'].forEach(function (evt) {
  dropzone.addEventListener(evt, function (e) {
    dropzone.classList.remove('dragover');
  });
});

dropzone.addEventListener('drop', function (e) {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (e.dataTransfer.files.length) {
    audioInput.files = e.dataTransfer.files;
    showChosenFile(e.dataTransfer.files[0]);
  }
});

let currentLines = [];
let timerInterval = null;
let startTime = null;

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? (m + 'm ' + rem + 's') : (rem + 's');
}

function pad(n, len) { return String(n).padStart(len, '0'); }

function toSRTTime(seconds) {
  if (seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.round((seconds - Math.floor(seconds)) * 1000);
  return pad(h, 2) + ':' + pad(m, 2) + ':' + pad(s, 2) + ',' + pad(ms, 3);
}

function toVTTTime(seconds) {
  return toSRTTime(seconds).replace(',', '.');
}

function toReadableTime(seconds) {
  if (seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = (seconds % 60).toFixed(2);
  const sPadded = s.length < 5 ? ('0' + s) : s;
  return h > 0 ? (pad(h, 2) + ':' + pad(m, 2) + ':' + sPadded) : (pad(m, 2) + ':' + sPadded);
}

function getCurrentTexts() {
  return Array.from(document.querySelectorAll('.line-text')).map(function (el) { return el.value; });
}

function buildSRT() {
  const texts = getCurrentTexts();
  const parts = currentLines.map(function (l, i) {
    return (i + 1) + '\n' + toSRTTime(l.start) + ' --> ' + toSRTTime(l.end) + '\n' + texts[i] + '\n';
  });
  return parts.join('\n');
}

function buildVTT() {
  const texts = getCurrentTexts();
  let out = 'WEBVTT\n\n';
  const parts = currentLines.map(function (l, i) {
    return toVTTTime(l.start) + ' --> ' + toVTTTime(l.end) + '\n' + texts[i] + '\n';
  });
  out += parts.join('\n');
  return out;
}

function buildTXT() {
  const texts = getCurrentTexts();
  const parts = currentLines.map(function (l, i) {
    return '[' + toReadableTime(l.start) + ' - ' + toReadableTime(l.end) + ']  ' + texts[i];
  });
  return parts.join('\n');
}

function downloadBlob(filename, content) {
  const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function renderLines() {
  linesDiv.innerHTML = '';
  currentLines.forEach(function (l, i) {
    const row = document.createElement('div');
    row.className = 'line-row';

    const timeDiv = document.createElement('div');
    timeDiv.className = 'line-time';
    timeDiv.innerHTML = toReadableTime(l.start) + '<br>' + toReadableTime(l.end);

    const textInput = document.createElement('input');
    textInput.className = 'line-text';
    textInput.value = l.text;

    row.appendChild(timeDiv);
    row.appendChild(textInput);
    linesDiv.appendChild(row);
  });
  lineCount.textContent = '(' + currentLines.length + ' lines -- edit any line before downloading)';
}

function handleTranscribe(e) {
  if (e) e.preventDefault();
  if (!form.elements['audio'].files.length) {
    status.textContent = 'Please choose an audio/video file first.';
    return;
  }
  btn.disabled = true;
  result.style.display = 'none';
  startTime = Date.now();
  status.textContent = 'Transcribing... (first run downloads the model, this can take a while)';
  timerInterval = setInterval(function () {
    status.textContent = 'Transcribing... elapsed: ' + fmtElapsed(Date.now() - startTime);
  }, 1000);

  const fd = new FormData(form);
  fetch('/transcribe', { method: 'POST', body: fd })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('Redirecting to login...');
      }
      if (!res.ok) {
        return res.json().then(function (err) {
          throw new Error(err.error || 'Failed');
        });
      }
      return res.json();
    })
    .then(function (data) {
      clearInterval(timerInterval);
      status.textContent = 'Done in ' + fmtElapsed(Date.now() - startTime) + ' -- ' + data.lines.length + ' lines, detected language: ' + data.language;
      currentLines = data.lines;
      renderLines();
      emptyState.style.display = 'none';
      result.style.display = 'block';
    })
    .catch(function (err) {
      clearInterval(timerInterval);
      status.textContent = 'Error: ' + err.message;
    })
    .finally(function () {
      btn.disabled = false;
    });
}

btn.addEventListener('click', handleTranscribe);
form.addEventListener('submit', function (e) {
  e.preventDefault();
  handleTranscribe();
});

document.getElementById('dl-srt').addEventListener('click', function () { downloadBlob('transcript.srt', buildSRT()); });
document.getElementById('dl-vtt').addEventListener('click', function () { downloadBlob('transcript.vtt', buildVTT()); });
document.getElementById('dl-txt').addEventListener('click', function () { downloadBlob('transcript.txt', buildTXT()); });
document.getElementById('copy-btn').addEventListener('click', function () {
  navigator.clipboard.writeText(getCurrentTexts().join(' '));
  const b = document.getElementById('copy-btn');
  const original = b.textContent;
  b.textContent = 'Copied!';
  setTimeout(function () { b.textContent = original; }, 1500);
});

// script loaded fine -- nothing to show

} catch (err) {
  const jc = document.getElementById('js-check');
  jc.style.display = 'block';
  jc.textContent = 'SCRIPT ERROR: ' + err.message;
  jc.style.color = '#f55';
  jc.style.fontWeight = 'bold';
  console.error(err);
}
