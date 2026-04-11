import React, { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../context/AuthContext'
import QRHealthCard from '../components/QRHealthCard'
import OfflineBanner from '../components/OfflineBanner'

export default function HealthCardPage() {
  const { apiFetch } = useAuth()
  const [cardData, setCardData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    apiFetch('/api/portal/health-card')
      .then(setCardData)
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
        <h1 className="text-lg font-bold text-gray-900">Health Card</h1>
      </header>

      <main className="px-4 py-8 max-w-sm mx-auto">
        {loading && <div className="text-center text-gray-400 py-12">Loading...</div>}

        {error && (
          <div className="bg-red-50 text-red-600 text-sm rounded-xl p-4">{error}</div>
        )}

        {cardData && (
          <div className="space-y-6">
            {/* Patient info */}
            <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5">
              <h2 className="text-xl font-bold text-gray-900">{cardData.name}</h2>
              <p className="text-sm text-gray-500 mt-1">ID: {cardData.patient_id}</p>

              {cardData.allergies && cardData.allergies.length > 0 && (
                <div className="mt-4">
                  <p className="text-xs font-semibold text-red-600 uppercase tracking-wide mb-2">
                    ⚠ Allergies
                  </p>
                  <div className="flex flex-wrap gap-2">
                    {cardData.allergies.map((a, i) => (
                      <span key={i} className="bg-red-50 text-red-700 text-xs font-medium px-3 py-1 rounded-full">
                        {a}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* QR Code */}
            <QRHealthCard data={cardData} />
          </div>
        )}
      </main>
    </div>
  )
}
