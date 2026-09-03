import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api.js'
import StageBadge from './StageBadge.jsx'

const stages = [
  { value: '', label: 'All Stages' },
  { value: 'discovery', label: 'Discovery' },
  { value: 'research', label: 'Research' },
  { value: 'script', label: 'Script' },
  { value: 'evidence_gate', label: 'Evidence Gate' },
  { value: 'human_review', label: 'Human Review' },
  { value: 'production', label: 'Production' },
  { value: 'assembly', label: 'Assembly' },
  { value: 'quality_gate', label: 'Quality Gate' },
  { value: 'packaging', label: 'Packaging' },
  { value: 'approval', label: 'Approval' },
  { value: 'published', label: 'Published' },
]

export default function ProductionList() {
  const [productions, setProductions] = useState([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => { load() }, [filter])

  async function load() {
    setLoading(true)
    try {
      const data = await api.listProductions(filter)
      setProductions(data)
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="container">
      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">Productions</div>
            <div className="header-sub">Review, approve, and track theological content</div>
          </div>
          <Link to="/new" className="btn btn-primary">+ New Production</Link>
        </div>

        <div className="filter-bar">
          {stages.map(s => (
            <button
              key={s.value}
              className={`btn ${filter === s.value ? 'btn-primary' : 'btn-outline'}`}
              onClick={() => setFilter(s.value)}
            >
              {s.label}
            </button>
          ))}
        </div>

        {loading && <div className="empty-state"><div className="spinner" /> Loading...</div>}
        {error && <div className="empty-state" style={{color:'var(--danger)'}}>Error: {error}</div>}

        {!loading && productions.length === 0 && (
          <div className="empty-state">No productions found.</div>
        )}

        {!loading && productions.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Topic</th>
                  <th>Category</th>
                  <th>Primary Scripture</th>
                  <th>Stage</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {productions.map(p => (
                  <tr key={p.id}>
                    <td>
                      <Link to={`/productions/${p.id}`} style={{fontWeight:600}}>
                        {p.topic}
                      </Link>
                    </td>
                    <td style={{textTransform:'capitalize'}}>{p.doctrinal_category?.replace(/_/g, ' ')}</td>
                    <td style={{fontFamily:'var(--font-serif)', fontSize:'0.8125rem'}}>{p.primary_scripture || '—'}</td>
                    <td><StageBadge stage={p.stage} /></td>
                    <td style={{color:'var(--text-muted)', fontSize:'0.8125rem'}}>
                      {new Date(p.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
