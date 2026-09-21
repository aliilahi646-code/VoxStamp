try {

const form = document.getElementById('f');
const status = document.getElementById('status');
const result = document.getElementById('result');
const btn = document.getElementById('transcribe-btn');
const dropzone = document.getElementById('dropzone');
const audioInput = document.getElementById('audio-input');
const dropzoneEmpty = document.getElementById('dropzone-empty');
const dropzoneFilename = document.getElementById('dropzone-filename');
const dropzoneFilenameText = document.getElementById('dropzone-filename-text');
const emptyState = document.getElementById('empty-state');
const resultTitle = document.getElementById('result-title');
const previewAudio = document.getElementById('preview-audio');
const previewVideo = document.getElementById('preview-video');
const previewImage = document.getElementById('preview-image');
const dlLink = document.getElementById('dl-link');
const targetFormat = document.getElementById('target-format');

let startTime = null;
let timerInterval = null;

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return m > 0 ? (m + 'm ' + rem + 's') : (rem + 's');
}

function showChosenFile(file) {
  if (!file) return;
  dropzoneEmpty.style.display = 'none';
  dropzoneFilename.style.display = 'flex';
  dropzoneFilenameText.textContent = file.name;
}

dropzone.addEventListener('click', function () { audioInput.click(); });
audioInput.addEventListener('change', function () {
  if (audioInput.files.length) showChosenFile(audioInput.files[0]);
});
['dragover', 'dragenter'].forEach(function (evt) {
  dropzone.addEventListener(evt, function (e) { e.preventDefault(); dropzone.classList.add('dragover'); });
});
['dragleave', 'dragend'].forEach(function (evt) {
  dropzone.addEventListener(evt, function () { dropzone.classList.remove('dragover'); });
});
dropzone.addEventListener('drop', function (e) {
  e.preventDefault();
  dropzone.classList.remove('dragover');
  if (e.dataTransfer.files.length) {
    audioInput.files = e.dataTransfer.files;
    showChosenFile(e.dataTransfer.files[0]);
  }
});

const videoFormats = ['mp4', 'webm', 'mov'];
const imageFormats = ['jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp'];
const audioFormats = ['mp3', 'wav', 'm4a', 'ogg', 'flac', 'aac'];

function handleConvert(e) {
  if (e) e.preventDefault();
  if (!audioInput.files.length) {
    status.textContent = 'Please choose a file first.';
    return;
  }
  btn.disabled = true;
  result.style.display = 'none';
  startTime = Date.now();
  status.textContent = 'Converting...';
  timerInterval = setInterval(function () {
    status.textContent = 'Converting... elapsed: ' + fmtElapsed(Date.now() - startTime);
  }, 1000);

  const fd = new FormData(form);
  fetch('/api/convert', { method: 'POST', body: fd })
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
      const downloadUrl = '/api/convert/download/' + data.job_id;
      resultTitle.textContent = data.filename;
      dlLink.href = downloadUrl;
      dlLink.setAttribute('download', data.filename);

      const isVideo = videoFormats.indexOf(data.format) !== -1;
      const isImage = imageFormats.indexOf(data.format) !== -1;
      const isAudio = audioFormats.indexOf(data.format) !== -1;
      previewAudio.style.display = 'none';
      previewVideo.style.display = 'none';
      previewImage.style.display = 'none';
      if (isImage) {
        previewImage.src = downloadUrl;
        previewImage.style.display = 'block';
      } else if (isVideo) {
        previewVideo.src = downloadUrl;
        previewVideo.style.display = 'block';
      } else if (isAudio) {
        previewAudio.src = downloadUrl;
        previewAudio.style.display = 'block';
      }
      // documents: no inline preview -- just the download link below.
      emptyState.style.display = 'none';
      result.style.display = 'block';
    })
    .catch(function (err) {
      clearInterval(timerInterval);
      status.textContent = 'Error: ' + err.message;
    })
    .finally(function () { btn.disabled = false; });
}

btn.addEventListener('click', handleConvert);
form.addEventListener('submit', function (e) { e.preventDefault(); handleConvert(); });

// script loaded fine -- nothing to show

} catch (err) {
  const jc = document.getElementById('js-check');
  jc.style.display = 'block';
  jc.textContent = 'SCRIPT ERROR: ' + err.message;
  jc.style.color = '#f55';
  jc.style.fontWeight = 'bold';
  console.error(err);
}
