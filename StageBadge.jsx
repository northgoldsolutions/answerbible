import React from 'react'

const stageMap = {
  discovery: 'badge-discovery',
  research: 'badge-research',
  script: 'badge-script',
  evidence_gate: 'badge-evidence',
  human_review: 'badge-human',
  production: 'badge-production',
  assembly: 'badge-assembly',
  quality_gate: 'badge-quality',
  packaging: 'badge-packaging',
  approval: 'badge-approval',
  published: 'badge-published',
  analytics: 'badge-analytics',
}

export default function StageBadge({ stage }) {
  const cls = stageMap[stage] || 'badge-discovery'
  return <span className={`badge ${cls}`}>{stage.replace(/_/g, ' ')}</span>
}
