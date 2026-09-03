import React, { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api.js'
import StageBadge from './StageBadge.jsx'
import StageProgress from './StageProgress.jsx'
import ClaimReview from './ClaimReview.jsx'
import SceneManager from './SceneManager.jsx'

const TABS = ['overview', 'claims', 'scenes', 'actions']

export default function ProductionDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [prod, setProd] = useState(null)
  const [tab, setTab] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState(false)
  const [reviewer, setReviewer] = useState('david')
  const [reviewNotes, setReviewNotes] = useState('')
  const [packaging, setPackaging] = useState({ title: '', description: '', keywords: '', thumbnail_prompt: '' })

  useEffect(() => { load() }, [id])

  async function load() {
    setLoading(true)
    try {
      const data = await api.getProduction(id)
      setProd(data)
      if (data.title) setPackaging(p => ({ ...p, title: data.title, description: data.description || '', keywords: data.keywords || '', thumbnail_prompt: data.thumbnail_prompt || '' }))
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function doAction(fn, ...args) {
    setActionLoading(true)
    try {
      await fn(...args)
      await load()
    } catch (e) {
      alert('Error: ' + e.message)
    } finally {
      setActionLoading(false)
    }
  }

  if (loading) return <div className="container empty-state"><div className="spinner" /> Loading...</div>
  if (error) return <div className="container empty-state" style={{color:'var(--danger)'}}>Error: {error}</div>
  if (!prod) return null

  const canRunEvidence = prod.stage === 'script'
  const canReview = prod.stage === 'evidence_gate'
  const canProduce = prod.stage === 'human_review'
  const canQuality = prod.stage === 'quality_gate'
  const canPackage = prod.stage === 'packaging'
  const canApprove = prod.stage === 'approval'

  return (
    <div className="container">
      <button className="btn btn-outline" onClick={() => navigate('/')} style={{marginBottom:16}}>← Back to list</button>

      <div className="card">
        <div className="card-header">
          <div>
            <div className="card-title">{prod.topic}</div>
            <div className="header-sub">
              {prod.primary_scripture && <span style={{marginRight:12}}>📖 {prod.primary_scripture}</span>}
              <span style={{textTransform:'capitalize'}}>{prod.doctrinal_category?.replace(/_/g,' ')}</span>
              {prod.gospel_video && <span style={{marginLeft:12, color:'var(--accent)'}}>⛪ Gospel Video</span>}
            </div>
          </div>
          <StageBadge stage={prod.stage} />
        </div>

        <StageProgress currentStage={prod.stage} />

        <div style={{display:'flex', gap:8, marginTop:12, flexWrap:'wrap'}}>
          <div className="badge" style={{background:'var(--surface-2)'}}>Evidence: {prod.evidence_gate_passed ? '✓' : '—'}</div>
          <div className="badge" style={{background:'var(--surface-2)'}}>Human: {prod.human_review_passed ? '✓' : '—'}</div>
          <div className="badge" style={{background:'var(--surface-2)'}}>Quality: {prod.quality_gate_passed ? '✓' : '—'}</div>
          {prod.approved_by && <div className="badge" style={{background:'var(--surface-2)'}}>Approved by {prod.approved_by}</div>}
        </div>
      </div>

      <div className="tabs">
        {TABS.map(t => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t === 'actions' && prod.requires_manual_review && <span style={{color:'var(--danger)', marginRight:4}}>●</span>}
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="card">
          <div className="card-title">Overview</div>
          {prod.hook && <div style={{marginBottom:12}}><strong>Hook:</strong> {prod.hook}</div>}
          {prod.problem && <div style={{marginBottom:12}}><strong>Problem:</strong> {prod.problem}</div>}
          {prod.explanation && <div style={{marginBottom:12}}><strong>Explanation:</strong> {prod.explanation}</div>}
          {prod.story && <div style={{marginBottom:12}}><strong>Story:</strong> {prod.story}</div>}
          {prod.application && <div style={{marginBottom:12}}><strong>Application:</strong> {prod.application}</div>}
          {prod.cta && <div><strong>CTA:</strong> {prod.cta}</div>}
          {!prod.hook && <div className="empty-state" style={{padding:24}}>No research submitted yet.</div>}
        </div>
      )}

      {tab === 'claims' && (
        <div className="card">
          <div className="card-header">
            <div className="card-title">Claims Review</div>
            {canRunEvidence && (
              <button className="btn btn-primary" onClick={() => doAction(api.runEvidenceGate, id)} disabled={actionLoading}>
                {actionLoading ? <div className="spinner" /> : 'Run Evidence Gate'}
              </button>
            )}
          </div>
          <ClaimReview claims={prod.claims} readOnly={!canRunEvidence} />
        </div>
      )}

      {tab === 'scenes' && (
        <div className="card">
          <div className="card-header">
            <div className="card-title">Scenes</div>
            {canProduce && (
              <button className="btn btn-primary" onClick={() => doAction(api.produce, id)} disabled={actionLoading}>
                {actionLoading ? <div className="spinner" /> : 'Start Production'}
              </button>
            )}
          </div>
          <SceneManager scenes={prod.scenes} />
        </div>
      )}

      {tab === 'actions' && (
        <div>
          {canReview && (
            <div className="card" style={{borderLeft:'3px solid var(--accent)'}}>
              <div className="card-title">🛡️ Human Review Required</div>
              <p style={{color:'var(--text-muted)', marginBottom:16, fontSize:'0.875rem'}}>
                This production has passed the automated evidence gate but requires your explicit approval before proceeding.
              </p>
              <div className="form-group">
                <label className="form-label">Reviewer</label>
                <input className="input" value={reviewer} onChange={e => setReviewer(e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Notes</label>
                <textarea className="textarea" value={reviewNotes} onChange={e => setReviewNotes(e.target.value)} placeholder="Approval notes..." />
              </div>
              <div style={{display:'flex', gap:8}}>
                <button className="btn btn-success" onClick={() => doAction(api.humanReview, id, { decision: 'pass', reviewer, notes: reviewNotes })} disabled={actionLoading}>
                  {actionLoading ? <div className="spinner" /> : '✓ Approve (David Approves)'}
                </button>
                <button className="btn btn-danger" onClick={() => doAction(api.humanReview, id, { decision: 'repair', reviewer, notes: reviewNotes })} disabled={actionLoading}>
                  Send Back for Repair
                </button>
              </div>
            </div>
          )}

          {canQuality && (
            <div className="card" style={{borderLeft:'3px solid var(--info)'}}>
              <div className="card-title">Quality Gate</div>
              <p style={{color:'var(--text-muted)', marginBottom:16, fontSize:'0.875rem'}}>
                Review the assembled video. Check audio, visuals, captions, and scripture accuracy.
              </p>
              <div className="form-group">
                <label className="form-label">Reviewer</label>
                <input className="input" value={reviewer} onChange={e => setReviewer(e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Notes</label>
                <textarea className="textarea" value={reviewNotes} onChange={e => setReviewNotes(e.target.value)} />
              </div>
              <div style={{display:'flex', gap:8}}>
                <button className="btn btn-success" onClick={() => doAction(api.qualityGate, id, { decision: 'pass', reviewer, notes: reviewNotes })} disabled={actionLoading}>
                  Pass Quality Gate
                </button>
                <button className="btn btn-danger" onClick={() => doAction(api.qualityGate, id, { decision: 'repair', reviewer, notes: reviewNotes })} disabled={actionLoading}>
                  Needs Repair
                </button>
              </div>
            </div>
          )}

          {canPackage && (
            <div className="card" style={{borderLeft:'3px solid var(--warning)'}}>
              <div className="card-title">Packaging</div>
              <div className="form-group">
                <label className="form-label">Title</label>
                <input className="input" value={packaging.title} onChange={e => setPackaging({...packaging, title: e.target.value})} />
              </div>
              <div className="form-group">
                <label className="form-label">Description</label>
                <textarea className="textarea" value={packaging.description} onChange={e => setPackaging({...packaging, description: e.target.value})} />
              </div>
              <div className="form-group">
                <label className="form-label">Keywords (comma separated)</label>
                <input className="input" value={packaging.keywords} onChange={e => setPackaging({...packaging, keywords: e.target.value})} />
              </div>
              <div className="form-group">
                <label className="form-label">Thumbnail Prompt</label>
                <input className="input" value={packaging.thumbnail_prompt} onChange={e => setPackaging({...packaging, thumbnail_prompt: e.target.value})} />
              </div>
              <button className="btn btn-primary" onClick={() => doAction(api.submitPackaging, id, packaging)} disabled={actionLoading}>
                Save Packaging
              </button>
            </div>
          )}

          {canApprove && (
            <div className="card" style={{borderLeft:'3px solid var(--success)'}}>
              <div className="card-title">Final Approval</div>
              <p style={{color:'var(--text-muted)', marginBottom:16, fontSize:'0.875rem'}}>
                All gates passed. This production is ready for publication.
              </p>
              <div className="form-group">
                <label className="form-label">Reviewer</label>
                <input className="input" value={reviewer} onChange={e => setReviewer(e.target.value)} />
              </div>
              <div className="form-group">
                <label className="form-label">Notes</label>
                <textarea className="textarea" value={reviewNotes} onChange={e => setReviewNotes(e.target.value)} />
              </div>
              <button className="btn btn-success" onClick={() => doAction(api.finalApproval, id, { decision: 'pass', reviewer, notes: reviewNotes })} disabled={actionLoading}>
                {actionLoading ? <div className="spinner" /> : '🚀 Final Approve & Publish'}
              </button>
            </div>
          )}

          {!canReview && !canQuality && !canPackage && !canApprove && (
            <div className="card empty-state" style={{padding:40}}>
              No actions available at stage <strong>{prod.stage.replace(/_/g,' ')}</strong>.
              <div style={{marginTop:8, color:'var(--text-muted)', fontSize:'0.875rem'}}>
                {prod.stage === 'discovery' && 'Submit research to advance.'}
                {prod.stage === 'research' && 'Submit script and claims to advance.'}
                {prod.stage === 'script' && 'Run the evidence gate.'}
                {prod.stage === 'production' && 'Production is running in background.'}
                {prod.stage === 'assembly' && 'Video is being assembled.'}
                {prod.stage === 'published' && 'This production has been published.'}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
