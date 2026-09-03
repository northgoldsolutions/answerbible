import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api.js'

const CATEGORIES = [
  { value: 'general', label: 'General' },
  { value: 'genesis_6', label: 'Genesis 6 / Nephilim' },
  { value: 'sheol', label: 'Sheol / Afterlife' },
  { value: 'spiritual_warfare', label: 'Spiritual Warfare' },
  { value: 'demons', label: 'Demons' },
  { value: 'election', label: 'Election / Predestination' },
  { value: 'end_times', label: 'End Times / Prophecy' },
  { value: 'divorce', label: 'Divorce / Remarriage' },
  { value: 'women_ministry', label: 'Women in Ministry' },
  { value: 'salvation', label: 'Salvation / Gospel' },
  { value: 'character_of_god', label: 'Character of God' },
  { value: 'prophecy_dating', label: 'Prophecy Date-Setting' },
]

export default function NewProduction() {
  const navigate = useNavigate()
  const [form, setForm] = useState({ topic: '', source_question: '', doctrinal_category: 'general', primary_scripture: '', gospel_video: false })
  const [loading, setLoading] = useState(false)

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    try {
      const data = await api.createProduction(form)
      navigate(`/productions/${data.id}`)
    } catch (err) {
      alert('Error: ' + err.message)
      setLoading(false)
    }
  }

  return (
    <div className="container">
      <button className="btn btn-outline" onClick={() => navigate('/')} style={{marginBottom:16}}>← Back</button>
      <div className="card" style={{maxWidth:600}}>
        <div className="card-title">New Production</div>
        <form onSubmit={submit}>
          <div className="form-group">
            <label className="form-label">Topic *</label>
            <input className="input" required value={form.topic} onChange={e => setForm({...form, topic: e.target.value})} />
          </div>
          <div className="form-group">
            <label className="form-label">Source Question</label>
            <input className="input" value={form.source_question} onChange={e => setForm({...form, source_question: e.target.value})} placeholder="e.g., YouTube comment asking..." />
          </div>
          <div className="form-group">
            <label className="form-label">Doctrinal Category</label>
            <select className="input" value={form.doctrinal_category} onChange={e => setForm({...form, doctrinal_category: e.target.value})}>
              {CATEGORIES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
            </select>
          </div>
          <div className="form-group">
            <label className="form-label">Primary Scripture</label>
            <input className="input" value={form.primary_scripture} onChange={e => setForm({...form, primary_scripture: e.target.value})} placeholder="e.g., Genesis 6:1-4" />
          </div>
          <div className="form-group" style={{display:'flex', alignItems:'center', gap:8}}>
            <input type="checkbox" id="gospel" checked={form.gospel_video} onChange={e => setForm({...form, gospel_video: e.target.checked})} />
            <label htmlFor="gospel" style={{fontSize:'0.875rem'}}>This is a Gospel / Salvation video (triggers extra scrutiny)</label>
          </div>
          <button className="btn btn-primary" type="submit" disabled={loading}>
            {loading ? <div className="spinner" /> : 'Create Production'}
          </button>
        </form>
      </div>
    </div>
  )
}
