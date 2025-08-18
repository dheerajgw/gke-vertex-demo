const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 8080;

// 1) Serve the built SPA from web/dist  ⬅️ THIS IS IMPORTANT
app.use(express.static(path.join(__dirname, '..', 'web', 'dist')));

// --- your APIs ---
app.get('/api/healthz', (req, res) => {
  res.json({ status: 'ok', message: 'Hello from GKE Vertex PoC!' });
});

// 2) Catch-all: send index.html so SPA routes work
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, '..', 'web', 'dist', 'index.html'));
});

if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`listening on :${PORT}`);
  });
}

module.exports = app;
