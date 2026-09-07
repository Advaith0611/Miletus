const API = (window.MILETUS_API_URL || '').replace(/\/$/, '') || 'http://localhost:8000';
const $ = (id) => document.getElementById(id);
const views = [$('upload-view'), $('progress-view'), $('result-view')];
const steps = [['extracting','Reading your documents'],['analyzing','Understanding the material'],['writing','Writing your podcast'],['generating_audio','Generating voices'],['assembling','Finalizing audio']];
let selected = [], timer;
function show(view) { views.forEach(v => v.classList.add('hidden')); view.classList.remove('hidden'); }
$('file-input').addEventListener('change', e => { selected = [...e.target.files]; renderFiles(); });
$('dropzone')?.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') $('file-input').click(); });
function renderFiles() { $('selected-files').textContent = selected.length ? `${selected.length} document${selected.length === 1 ? '' : 's'} selected` : ''; $('start-button').disabled = !selected.length; }
$('upload-form').addEventListener('submit', async e => { e.preventDefault(); if (!selected.length) return; const data = new FormData(); selected.forEach(file => data.append('files', file)); show($('progress-view')); try { const response = await fetch(`${API}/api/generate`, {method:'POST', body:data}); if (!response.ok) throw new Error(); const job = await response.json(); timer = setInterval(() => poll(job.job_id), 700); poll(job.job_id); } catch { fail(); } });
async function poll(id) { try { const response = await fetch(`${API}/api/jobs/${id}`); if (!response.ok) throw new Error(); const job = await response.json(); updateProgress(job); if (job.status === 'complete') { clearInterval(timer); await finish(job); } if (job.status === 'failed') { clearInterval(timer); fail(job.error); } } catch { clearInterval(timer); fail(); } }
function updateProgress(job) { $('progress-message').textContent = job.message; $('progress-bar').style.width = `${job.progress}%`; $('steps').innerHTML = steps.map(([key,label]) => { const index=steps.findIndex(s=>s[0]===job.status), own=steps.findIndex(s=>s[0]===key); const state=own<index?'done':own===index?'active':''; return `<div class="step ${state}"><span>${state==='done'?'✓':state==='active'?'●':'○'}</span>${label}</div>`; }).join(''); }
async function finish(job) { show($('result-view')); $('podcast-title').textContent = job.title || 'Your podcast'; $('audio-player').src = `${API}${job.audio_url}`; $('download-link').href = `${API}${job.audio_url}`; try { $('transcript-text').textContent = await (await fetch(`${API}${job.transcript_url}`)).text(); } catch {} }
function fail(details) { $('progress-title').textContent = 'Something went wrong.'; $('progress-message').textContent = details || 'Please try again.'; $('cancel-button').textContent = 'Try again'; }
function reset() { selected=[]; $('file-input').value=''; renderFiles(); show($('upload-view')); }
$('cancel-button').addEventListener('click', reset); $('again-button').addEventListener('click', reset);
