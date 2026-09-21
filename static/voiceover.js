try {

const form = document.getElementById('f');
const status = document.getElementById('status');
const result = document.getElementById('result');
const btn = document.getElementById('transcribe-btn');
const emptyState = document.getElementById('empty-state');
const scriptText = document.getElementById('script-text');
const charCount = document.getElementById('char-count');
const rateSlider = document.getElementById('rate-slider');
const rateLabel = document.getElementById('rate-label');
const previewAudio = document.getElementById('preview-audio');
const dlLink = document.getElementById('dl-link');
const previewVoiceBtn = document.getElementById('preview-voice-btn');
const previewVoiceAudio = document.getElementById('preview-voice-audio');
const voiceSelect = document.getElementById('voice-select');

let startTime = null;
let timerInterval = null;

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? (m + 'm ' + rem + 's') : (rem + 's');
}

scriptText.addEventListener('input', function () {
  charCount.textContent = scriptText.value.length + ' characters';
});

rateSlider.addEventListener('input', function () {
  const v = parseInt(rateSlider.value, 10);
  if (v === 0) rateLabel.textContent = 'Normal speed';
  else if (v > 0) rateLabel.textContent = v + '% faster';
  else rateLabel.textContent = Math.abs(v) + '% slower';
});

function handleGenerate(e) {
  if (e) e.preventDefault();
  if (!scriptText.value.trim()) {
    status.textContent = 'Please write or paste a script first.';
    return;
  }
  btn.disabled = true;
  result.style.display = 'none';
  startTime = Date.now();
  status.textContent = 'Generating voiceover...';
  timerInterval = setInterval(function () {
    status.textContent = 'Generating voiceover... elapsed: ' + fmtElapsed(Date.now() - startTime);
  }, 1000);

  const fd = new FormData(form);
  fetch('/api/voiceover', { method: 'POST', body: fd })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('Redirecting to login...');
      }
      if (!res.ok) {
        return res.json().then(function (err) { throw new Error(err.error || 'Failed'); });
      }
      return res.json();
    })
    .then(function (data) {
      clearInterval(timerInterval);
      status.textContent = 'Done in ' + fmtElapsed(Date.now() - startTime);
      const downloadUrl = '/api/voiceover/download/' + data.job_id;
      previewAudio.src = downloadUrl;
      dlLink.href = downloadUrl;
      dlLink.setAttribute('download', data.filename);
      emptyState.style.display = 'none';
      result.style.display = 'block';
    })
    .catch(function (err) {
      clearInterval(timerInterval);
      status.textContent = 'Error: ' + err.message;
    })
    .finally(function () { btn.disabled = false; });
}

function handlePreviewVoice() {
  const originalText = previewVoiceBtn.textContent;
  previewVoiceBtn.disabled = true;
  previewVoiceBtn.textContent = 'Loading preview...';

  const fd = new FormData();
  fd.append('voice', voiceSelect.value);

  fetch('/api/voice-preview', { method: 'POST', body: fd })
    .then(function (res) {
      if (res.status === 401) {
        window.location.href = '/login';
        throw new Error('Redirecting to login...');
      }
      if (!res.ok) {
        return res.json().then(function (err) { throw new Error(err.error || 'Failed'); });
      }
      return res.json();
    })
    .then(function (data) {
      previewVoiceAudio.src = '/api/voiceover/download/' + data.job_id;
      previewVoiceAudio.play();
    })
    .catch(function (err) {
      status.textContent = 'Preview error: ' + err.message;
    })
    .finally(function () {
      previewVoiceBtn.disabled = false;
      previewVoiceBtn.textContent = originalText;
    });
}

previewVoiceBtn.addEventListener('click', handlePreviewVoice);

btn.addEventListener('click', handleGenerate);
form.addEventListener('submit', function (e) { e.preventDefault(); handleGenerate(); });

// script loaded fine -- nothing to show

} catch (err) {
  const jc = document.getElementById('js-check');
  jc.style.display = 'block';
  jc.textContent = 'SCRIPT ERROR: ' + err.message;
  jc.style.color = '#f55';
  jc.style.fontWeight = 'bold';
  console.error(err);
}
