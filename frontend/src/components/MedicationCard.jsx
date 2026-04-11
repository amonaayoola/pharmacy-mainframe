import React, { useState } from 'react'
import { useAuth } from '../context/AuthContext'

export default function MedicationCard({ med, onRefillRequested }) {
  const { apiFetch } = useAuth()
  const [loading, setLoading] = useState(false)
  const [requested, setRequested] = useState(false)
  const [error, setError] = useState('')

  // Burn-down: percentage remaining
  const pct = med.days_supply > 0
    ? Math.min(100, Math.round((med.days_remaining / med.days_supply) * 100))
    : 0

  const barColor =
    pct > 50 ? 'bg-green-500' :
    pct > 25 ? 'bg-amber-500' :
    'bg-red-500'

  async function handleRefill() {
    setLoading(true)
    setError('')
    try {
      await apiFetch('/api/portal/refill-request', {
        method: 'POST',
        body: JSON.stringify({ medication_id: med.id }),
      })
      setRequested(true)
      onRefillRequested?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-5">
      <div className="flex items-start justify-between mb-3">
        <div>
          <h3 className="font-semibold text-gray-900">{med.drug_name}</h3>
          <p className="text-sm text-gray-500">{med.dosage}</p>
        </div>
        <span className="text-sm font-medium text-gray-700 bg-gray-100 px-2 py-1 rounded-lg">
          Qty: {med.quantity_remaining}
        </span>
      </div>

      {/* Burn-down bar */}
      <div className="mb-1">
        <div className="flex justify-between text-xs text-gray-400 mb-1">
          <span>{med.days_remaining} days left</span>
          <span>{med.days_supply} day supply</span>
        </div>
        <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
          <div className={`h-full ${barColor} rounded-full transition-all`} style={{ width: `${pct}%` }} />
        </div>
      </div>

      {error && <p className="text-xs text-red-600 mt-2">{error}</p>}

      <button
        onClick={handleRefill}
        disabled={loading || requested}
        className="mt-4 w-full text-sm font-medium py-2 rounded-xl border border-brand-500 text-brand-500 hover:bg-brand-50 disabled:opacity-50 transition-colors"
      >
        {requested ? '✓ Refill Requested' : loading ? 'Requesting...' : 'Request Refill'}
      </button>
    </div>
  )
}
