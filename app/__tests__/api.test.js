const request = require('supertest');
const app = require('../api/server');

describe('health', () => {
  it('returns ok + message', async () => {
    const res = await request(app).get('/api/healthz');
    expect(res.status).toBe(200);
    expect(res.body.status).toBe('ok');
    expect(res.body.message).toContain('Hello from GKE Vertex PoC!');
  });
});
