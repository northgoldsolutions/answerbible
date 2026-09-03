import React from 'react'
import { Routes, Route, Link } from 'react-router-dom'
import ProductionList from './components/ProductionList.jsx'
import ProductionDetail from './components/ProductionDetail.jsx'
import NewProduction from './components/NewProduction.jsx'

export default function App() {
  return (
    <div>
      <header className="header-bar">
        <div>
          <div className="header-title">Answers in Faith</div>
          <div className="header-sub">Theological Review Dashboard</div>
        </div>
        <nav style={{display:'flex', gap:16, fontSize:'0.875rem'}}>
          <Link to="/" style={{color:'var(--text)'}}>Productions</Link>
          <Link to="/new" style={{color:'var(--accent)'}}>+ New</Link>
        </nav>
      </header>

      <Routes>
        <Route path="/" element={<ProductionList />} />
        <Route path="/new" element={<NewProduction />} />
        <Route path="/productions/:id" element={<ProductionDetail />} />
      </Routes>
    </div>
  )
}
