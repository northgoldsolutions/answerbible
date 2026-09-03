import React from 'react'

const steps = [
  'discovery', 'research', 'script', 'evidence_gate', 'human_review',
  'production', 'assembly', 'quality_gate', 'packaging', 'approval', 'published'
]

export default function StageProgress({ currentStage }) {
  const idx = steps.indexOf(currentStage)
  return (
    <div className="pipeline">
      {steps.map((step, i) => (
        <React.Fragment key={step}>
          <div className={`pipeline-step ${i < idx ? 'done' : ''} ${i === idx ? 'current' : ''}`}>
            {step.replace(/_/g, ' ')}
          </div>
          {i < steps.length - 1 && <span className="pipeline-arrow">→</span>}
        </React.Fragment>
      ))}
    </div>
  )
}
