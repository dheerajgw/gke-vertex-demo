const express = require('express');
const path = require('path');
const client = require('prom-client');

const app = express();
const port = process.env.PORT || 8080;

// Basic Prometheus metrics
const register = new client.Registry();
client.collectDefaultMetrics({ register });
const httpCounter = new client.Counter({
  name: 'http_requests_total',
  help: 'Total HTTP requests',
  labelNames: ['route', 'method', 'status']
});
register.registerMetric(httpCounter);

// Static UI
app.use(express.static(path.join(__dirname, '..', 'public')));

// API routes
app.get('/api/version', (req, res) => {
  httpCounter.inc({ route: '/api/version', method: 'GET', status: 200 });
  res.json({
    name: 'gke-vertex-fancy-app',
    version: '1.0.0',
    commit: process.env.GIT_COMMIT || 'dev',
    env: process.env.NODE_ENV || 'local'
  });
});

// Intentional tiny bug: key is 'messege' instead of 'message'
app.get('/api/healthz', (req, res) => {
  httpCounter.inc({ route: '/api/healthz', method: 'GET', status: 200 });
  res.json({ status: 'ok', messege: 'Hello from GKE Vertex PoC!' });
});

// Prometheus metrics endpoint
app.get('/metrics', async (req, res) => {
  try {
    const metrics = await register.metrics();
    res.set('Content-Type', register.contentType);
    res.send(metrics);
  } catch (e) {
    res.status(500).send('metrics error');
  }
});

// Root serves the dashboard
app.get('/', (req, res) => {
  httpCounter.inc({ route: '/', method: 'GET', status: 200 });
  res.sendFile(path.join(__dirname, '..', 'public', 'index.html'));
});

if (require.main === module) {
  app.listen(port, () => {
    console.log(`Fancy app listening on http://0.0.0.0:${port}`);
  });
}

module.exports = app; // for tests
