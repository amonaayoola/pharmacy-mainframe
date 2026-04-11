import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import MedicationCard from '../components/MedicationCard'
import OfflineBanner from '../components/OfflineBanner'

export default function DashboardPage() {
  const { patient, apiFetch, logout } = useAuth()
  const [medications, setMedications] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  async function loadData() {
    setLoading(true)
    try {
      const data = await apiFetch('/api/portal/me')
      setMedications(data.medications || [])
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadData() }, [])

  return (
    <div className="min-h-screen bg-gray-50">
      <OfflineBanner />

      {/* Header */}
      <header className="bg-white border-b border-gray-100 px-4 py-4 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-bold text-gray-900">
            {patient?.name ? `Hello, ${patient.name.split(' ')[0]}` : 'My Medications'}
          </h1>
          <p className="text-xs text-gray-400">HealthBridge Patient Portal</p>
        </div>
        <button onClick={logout} className="text-xs text-gray-400 hover:text-gray-600">
          Sign out
        </button>
      </header>

      <main className="px-4 py-6 max-w-lg mx-auto space-y-4">
        {/* Nav links */}
        <div className="flex gap-2">
          <Link
            to="/refills"
            className="flex-1 text-center text-sm font-medium py-2 rounded-xl bg-white border border-gray-200 text-gray-600 hover:bg-gray-50"
          >
            Refill History
          </Link>
        </div>

        {loading && (
          <div className="text-center py-12 text-gray-400">Loading medications...</div>
        )}

        {error && (
          <div className="bg-red-50 text-red-600 text-sm rounded-xl p-4">{error}</div>
        )}

        {!loading && !error && medications.length === 0 && (
          <div className="text-center py-12 text-gray-400">No active medications found.</div>
        )}

        {medications.map((med) => (
          <MedicationCard key={med.id} med={med} onRefillRequested={loadData} />
        ))}
      </main>

      {/* Floating Health Card button */}
      <Link
        to="/health-card"
        className="fixed bottom-6 right-6 bg-brand-500 text-white rounded-2xl px-5 py-3 shadow-lg flex items-center gap-2 font-semibold text-sm hover:bg-brand-600 transition-colors"
      >
        <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M10 6H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V8a2 2 0 00-2-2h-5m-4 0V5a2 2 0 114 0v1m-4 0a2 2 0 104 0" />
        </svg>
        Health Card
      </Link>
    </div>
  )
}
