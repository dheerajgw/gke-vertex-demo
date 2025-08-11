const request = require('supertest');
const app = require('../src/server');

describe('health endpoint', () => {
  it('returns ok and correct message key', async () => {
    const res = await request(app).get('/api/healthz');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
    // Test expects 'message' but the app returns 'messege' (intentional first-fail)
    expect(res.body.message).toContain('Hello from GKE Vertex PoC!');
  });
});

