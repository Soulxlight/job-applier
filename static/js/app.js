// Select all checkbox
const selectAll = document.getElementById('selectAll');
if (selectAll) {
  selectAll.addEventListener('change', () => {
    document.querySelectorAll('.job-check').forEach(cb => cb.checked = selectAll.checked);
  });
}

// Status polling
let polling = null;

function startScrape() {
  fetch('/api/scrape', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d.error) { alert(d.error); return; }
      showStatusBar();
      startPolling();
    });
}

function startApply() {
  if (!confirm('Apply to all approved jobs now?')) return;
  fetch('/api/apply', { method: 'POST' })
    .then(r => r.json())
    .then(d => {
      if (d.error) { alert(d.error); return; }
      showStatusBar();
      startPolling();
    });
}

function showStatusBar() {
  const bar = document.getElementById('statusBar');
  if (bar) bar.style.display = 'block';
}

function startPolling() {
  if (polling) clearInterval(polling);
  polling = setInterval(() => {
    fetch('/api/status')
      .then(r => r.json())
      .then(status => {
        const msg = document.getElementById('statusMsg');
        if (msg) msg.textContent = status.message;

        const fill = document.querySelector('.progress-fill');
        if (fill && status.total) {
          fill.style.width = Math.round(status.progress / status.total * 100) + '%';
        }

        if (!status.running) {
          clearInterval(polling);
          polling = null;
          // Reload to show updated stats
          setTimeout(() => location.reload(), 1500);
        }
      });
  }, 1500);
}

function toggleNotes(appId) {
  const row = document.getElementById('notes-' + appId);
  if (row) row.style.display = row.style.display === 'none' ? 'table-row' : 'none';
}

// Auto-start polling if the page loads while running
(function () {
  const bar = document.getElementById('statusBar');
  if (bar && bar.style.display !== 'none') startPolling();
})();
