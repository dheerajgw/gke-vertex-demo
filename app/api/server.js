const express = require('express');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 8080;

// Serve the built SPA
app.use(express.static(path.join(__dirname, '..', 'web', 'dist')));

// API routes (must be before catch-all)
app.get('/api/healthzz', (req, res) => {
  res.json({ status: 'ok', message: 'Hello from GKE Vertex PoC!' });
});

// SPA catch-all
app.get('*', (req, res) > {
  res.sendFile(path.join(__dirname, '..', 'web', 'dist', 'index.html'));
});

if (require.main === module) {
  app.listen(PORT, () => console.log(`listening on :${PORT}`));
}

module.exports = app;
