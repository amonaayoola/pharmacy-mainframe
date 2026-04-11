import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import OfflineBanner from '../components/OfflineBanner'

const STATUS_STYLES = {
  pending:   'bg-amber-100 text-amber-700',
  approved:  'bg-blue-100 text-blue-700',
  dispensed: 'bg-green-100 text-green-700',
  cancelled: 'bg-gray-100 text-gray-500',
}

function StatusBadge({ status }) {
  const s = (status || 'pending').toLowerCase()
  return (
    <span className={`text-xs font-semibold px-3 py-1 rounded-full ${STATUS_STYLES[s] || 'bg-gray-100 text-gray-500'}`}>
      {s.charAt(0).toUpperCase() + s.slice(1)}
    </span>
  )
}

export default function RefillHistoryPage() {
  const { apiFetch } = useAuth()
  const [refills, setRefills] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    apiFetch('/api/portal/refill-requests')
      .then((data) => setRefills(Array.isArray(data) ? data : data.results || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false))
  }, [])

  return (
    <div className="min-h-screen bg-gray-50">
      <OfflineBanner />

      <header className="bg-white border-b border-gray-100 px-4 py-4 flex items-center gap-3">
        <Link to="/dashboard" className="text-gray-400 hover:text-gray-600">
          <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
        </Link>
        <h1 className="text-lg font-bold text-gray-900">Refill History</h1>
      </header>

      <main className="px-4 py-6 max-w-lg mx-auto space-y-3">
        {loading && <div className="text-center text-gray-400 py-12">Loading...</div>}
        {error && <div className="bg-red-50 text-red-600 text-sm rounded-xl p-4">{error}</div>}

        {!loading && !error && refills.length === 0 && (
          <div className="text-center py-12 text-gray-400">No refill requests yet.</div>
        )}

        {refills.map((r) => (
          <div key={r.id} className="bg-white rounded-2xl border border-gray-100 shadow-sm p-4 flex items-center justify-between">
            <div>
              <p className="font-medium text-gray-900">{r.drug_name || r.medication_name}</p>
              <p className="text-xs text-gray-400 mt-0.5">
                {r.requested_at ? new Date(r.requested_at).toLocaleDateString('en-NG', {
                  day: 'numeric', month: 'short', year: 'numeric'
                }) : ''}
              </p>
            </div>
            <StatusBadge status={r.status} />
          </div>
        ))}
      </main>
    </div>
  )
}
