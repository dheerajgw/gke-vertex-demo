import { useState, useEffect } from 'react'
import Home from './pages/Home.jsx'
import About from './pages/About.jsx'

export default function App(){
  const [route,setRoute]=useState('home')
  const [health,setHealth]=useState(null)
  useEffect(()=>{ fetch('/api/healthz').then(r=>r.json()).then(setHealth).catch(()=>setHealth({error:true})) },[])
  return (
    <div style={{fontFamily:'Inter, system-ui', padding:20}}>
      <h1>Agentic AI Auto‑Healing PoC</h1>
      <nav style={{display:'flex',gap:10, marginBottom:10}}>
        <button onClick={()=>setRoute('home')}>Home</button>
        <button onClick={()=>setRoute('about')}>About</button>
        <a href="/api/healthz" target="_blank" rel="noreferrer">/api/healthz</a>
      </nav>
      {route==='home' ? <Home health={health}/> : <About/>}
    </div>
  )
}
