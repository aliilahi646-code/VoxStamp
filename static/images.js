try {

const form = document.getElementById('f');
const status = document.getElementById('status');
const result = document.getElementById('result');
const btn = document.getElementById('transcribe-btn');
const emptyState = document.getElementById('empty-state');
const promptsText = document.getElementById('prompts-text');
const promptCount = document.getElementById('prompt-count');
const lineCount = document.getElementById('line-count');
const imageGrid = document.getElementById('image-grid');
const dlZip = document.getElementById('dl-zip');

let startTime = null;
let timerInterval = null;

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? (m + 'm ' + rem + 's') : (rem + 's');
}

function countPrompts() {
  const lines = promptsText.value.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
  promptCount.textContent = lines.length + ' prompt' + (lines.length === 1 ? '' : 's');
}
promptsText.addEventListener('input', countPrompts);
countPrompts();

function renderResults(jobId, results) {
  imageGrid.innerHTML = '';
  results.forEach(function (r) {
    const card = document.createElement('div');
    card.className = 'image-card' + (r.ok ? '' : ' failed');

    if (r.ok) {
      const img = document.createElement('img');
      img.src = '/api/images/download/' + jobId + '/' + r.filename;
      img.alt = r.prompt;
      card.appendChild(img);
    }

    const body = document.createElement('div');
    body.className = 'image-card-body';

    const promptEl = document.createElement('div');
    promptEl.className = 'image-card-prompt';
    promptEl.textContent = r.ok ? r.prompt : (r.prompt + ' -- failed: ' + (r.error || 'unknown error'));
    body.appendChild(promptEl);

    if (r.ok) {
      const actions = document.createElement('div');
      actions.className = 'image-card-actions';
      const dl = document.createElement('a');
      dl.href = '/api/images/download/' + jobId + '/' + r.filename;
      dl.download = r.filename;
      dl.textContent = 'Download';
      actions.appendChild(dl);
      body.appendChild(actions);
    }

    card.appendChild(body);
    imageGrid.appendChild(card);
  });
}

function handleGenerate(e) {
  if (e) e.preventDefault();
  const lines = promptsText.value.split('\n').map(function (l) { return l.trim(); }).filter(Boolean);
  if (!lines.length) {
    status.textContent = 'Please enter at least one prompt.';
    return;
  }
  btn.disabled = true;
  result.style.display = 'none';
  startTime = Date.now();
  status.textContent = 'Generating ' + lines.length + ' image' + (lines.length === 1 ? '' : 's') + '... this can take a while for larger batches.';
  timerInterval = setInterval(function () {
    status.textContent = 'Generating... elapsed: ' + fmtElapsed(Date.now() - startTime);
  }, 1000);

  const fd = new FormData(form);
  fetch('/api/generate-images', { method: 'POST', body: fd })
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
      status.textContent = 'Done in ' + fmtElapsed(Date.now() - startTime) + ' -- ' + data.ok_count + '/' + data.total + ' succeeded';
      lineCount.textContent = '(' + data.ok_count + '/' + data.total + ' succeeded)';
      renderResults(data.job_id, data.results);
      dlZip.href = '/api/images/download/' + data.job_id + '/images.zip';
      emptyState.style.display = 'none';
      result.style.display = 'block';
    })
    .catch(function (err) {
      clearInterval(timerInterval);
      status.textContent = 'Error: ' + err.message;
    })
    .finally(function () { btn.disabled = false; });
}

btn.addEventListener('click', handleGenerate);
form.addEventListener('submit', function (e) { e.preventDefault(); handleGenerate(); });

} catch (err) {
  const jc = document.getElementById('js-check');
  if (jc) {
    jc.style.display = 'block';
    jc.textContent = 'SCRIPT ERROR: ' + err.message;
    jc.style.color = '#f55';
    jc.style.fontWeight = 'bold';
  }
  console.error(err);
}
