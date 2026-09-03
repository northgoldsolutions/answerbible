import React from 'react'

export default function SceneManager({ scenes, onReorder }) {
  const move = (index, dir) => {
    if (!onReorder) return
    const newScenes = [...scenes]
    const swapWith = index + dir
    if (swapWith < 0 || swapWith >= newScenes.length) return
    const temp = newScenes[index]
    newScenes[index] = newScenes[swapWith]
    newScenes[swapWith] = temp
    onReorder(newScenes)
  }

  return (
    <div>
      {scenes.map((scene, i) => (
        <div key={scene.id} className="scene-item">
          <div className="scene-num">{i + 1}</div>
          <div className="scene-body">
            <div className="scene-narration">{scene.narration_text || '(no narration)'}</div>
            <div className="scene-visual">{scene.visual_prompt || '(no visual prompt)'}</div>
            <div style={{marginTop:6, display:'flex', gap:8, alignItems:'center'}}>
              <span className={`badge badge-${scene.status}`}>{scene.status}</span>
              {scene.locked && <span className="badge" style={{background:'var(--surface-2)'}}>Locked</span>}
            </div>
          </div>
          {onReorder && (
            <div style={{display:'flex', flexDirection:'column', gap:4}}>
              <button className="btn btn-outline" style={{padding:'4px 8px'}} onClick={() => move(i, -1)} disabled={i===0}>↑</button>
              <button className="btn btn-outline" style={{padding:'4px 8px'}} onClick={() => move(i, 1)} disabled={i===scenes.length-1}>↓</button>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
