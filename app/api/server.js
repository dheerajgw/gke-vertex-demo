const express = require('express')
const path = require('path')
const app = express()
const port = process.env.PORT || 8080

app.use(express.static(path.join(__dirname, '..', 'web', 'dist')))

app.get('/api/version', (_req,res)=> {
  res.json({ name:'gke-vertex-fancy-app', version:'1.0.0' })
})

// Healthy version (we'll break this later to trigger auto-heal)
app.get('/api/healthz', (_req,res)=> {
  res.json({ status:'ok', messege:'Hello from GKE Vertex PoC!' })
})

app.get('*', (_req,res)=> {
  res.sendFile(path.join(__dirname, '..', 'web', 'dist', 'index.html'))
})

if (require.main === module) {
  app.listen(port, ()=> console.log(`App http://0.0.0.0:${port}`))
}
module.exports = app
