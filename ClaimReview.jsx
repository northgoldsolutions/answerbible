import React, { useState } from 'react'

function ClaimCard({ claim, onAction }) {
  const [notes, setNotes] = useState('')
  const statusClass = claim.status === 'pass' ? 'pass' : claim.status === 'fail' ? 'fail' : 'pending'

  return (
    <div className={`claim-card ${statusClass}`}>
      <div className="claim-header">
        <div style={{flex:1}}>
          <div className="claim-text">{claim.text}</div>
          <div className="claim-meta">
            <span className={`badge badge-${claim.confidence}`}>{claim.confidence}</span>
            {' '}
            <span className={`badge badge-${claim.type}`}>{claim.type.replace(/_/g,' ')}</span>
            {' '}
            <span className={`badge badge-${claim.status}`}>{claim.status}</span>
          </div>
        </div>
      </div>

      {claim.source_reference && (
        <div className="scripture-box">
          <div className="scripture-ref">{claim.source_reference}</div>
          <div style={{fontStyle:'italic', opacity:0.9}}>{claim.source_text}</div>
        </div>
      )}

      {claim.context && (
        <div style={{margin:'8px 0', fontSize:'0.8125rem', color:'var(--text-muted)'}}>
          <strong style={{color:'var(--text)'}}>Context:</strong> {claim.context}
        </div>
      )}

      {claim.interpretation && (
        <div style={{margin:'8px 0', fontSize:'0.8125rem', color:'var(--text-muted)'}}>
          <strong style={{color:'var(--text)'}}>Interpretation:</strong> {claim.interpretation}
        </div>
      )}

      {claim.alternative_interpretations && (
        <div style={{margin:'8px 0', fontSize:'0.8125rem', color:'var(--text-muted)'}}>
          <strong style={{color:'var(--text)'}}>Alternative Views:</strong> {claim.alternative_interpretations}
        </div>
      )}

      {claim.cross_references?.length > 0 && (
        <div style={{margin:'8px 0', fontSize:'0.8125rem'}}>
          <strong style={{color:'var(--text)'}}>Cross-refs:</strong> {claim.cross_references.join(', ')}
        </div>
      )}

      {claim.evidence_notes && claim.status === 'fail' && (
        <div className="violation-box">
          <strong>Gate Violation:</strong> {claim.evidence_notes}
        </div>
      )}

      {claim.status === 'pending' && onAction && (
        <div style={{display:'flex', gap:8, marginTop:12, alignItems:'flex-start'}}>
          <textarea
            className="textarea"
            style={{flex:1, minHeight:60, fontSize:'0.8125rem'}}
            placeholder="Repair notes (required if rejecting)..."
            value={notes}
            onChange={e => setNotes(e.target.value)}
          />
          <div style={{display:'flex', flexDirection:'column', gap:6}}>
            <button className="btn btn-success" onClick={() => onAction(claim.id, 'pass', notes)}>
              Pass
            </button>
            <button className="btn btn-danger" onClick={() => onAction(claim.id, 'repair', notes)}>
              Repair
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function ClaimReview({ claims, readOnly }) {
  const [localClaims, setLocalClaims] = useState(claims)

  const handleAction = (claimId, decision, notes) => {
    setLocalClaims(prev => prev.map(c =>
      c.id === claimId ? { ...c, status: decision, evidence_notes: notes || c.evidence_notes } : c
    ))
  }

  const passed = localClaims.filter(c => c.status === 'pass').length
  const failed = localClaims.filter(c => c.status === 'fail').length

  return (
    <div>
      <div style={{display:'flex', gap:12, marginBottom:16, fontSize:'0.875rem'}}>
        <span><strong style={{color:'var(--success)'}}>{passed}</strong> Passed</span>
        <span><strong style={{color:'var(--danger)'}}>{failed}</strong> Failed</span>
        <span><strong>{localClaims.length - passed - failed}</strong> Pending</span>
      </div>
      {localClaims.map(claim => (
        <ClaimCard key={claim.id} claim={claim} onAction={readOnly ? null : handleAction} />
      ))}
    </div>
  )
}
