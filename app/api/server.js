// app/api/server.js
const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 8080;

// Serve the built SPA from web/dist
app.use(express.static(path.join(__dirname, '..', 'web', 'dist')));

// --- APIs (define BEFORE the catch-all) ---
app.get('/api/healthz', (req, res) => {
  res.json({ status: 'ok', message: 'Hello from GKE Vertex PoC!' });
});

// Catch-all: send index.html so SPA routes work on refresh
app.get('*', (req, res) => {
  res.sendFile(path.join(__dirname, '..', 'web', 'dist', 'index.html'));
});

// Start only if run directly (Jest imports the app without listening)
if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`listening on :${PORT}`);
  });
}

module.exports = app;
