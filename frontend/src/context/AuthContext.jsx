import React, { createContext, useContext, useState, useCallback } from 'react'

const AuthContext = createContext(null)

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

export function AuthProvider({ children }) {
  const [token, setToken] = useState(null)
  const [patient, setPatient] = useState(null)

  const apiFetch = useCallback(
    async (path, options = {}) => {
      const headers = {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(options.headers || {}),
      }
      const res = await fetch(`${BASE_URL}${path}`, { ...options, headers })
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: 'Request failed' }))
        throw new Error(err.detail || `HTTP ${res.status}`)
      }
      return res.json()
    },
    [token]
  )

  const login = useCallback((jwt, patientData) => {
    setToken(jwt)
    setPatient(patientData)
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setPatient(null)
  }, [])

  return (
    <AuthContext.Provider value={{ token, patient, setPatient, login, logout, apiFetch }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
