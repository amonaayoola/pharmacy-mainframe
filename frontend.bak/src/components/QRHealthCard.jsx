import React from 'react'
import { QRCodeSVG } from 'qrcode.react'

export default function QRHealthCard({ data }) {
  const qrValue = JSON.stringify(data)

  return (
    <div className="flex flex-col items-center">
      <div className="bg-white p-4 rounded-2xl shadow-sm border border-gray-100">
        <QRCodeSVG value={qrValue} size={200} level="M" includeMargin />
      </div>
      <p className="text-sm text-gray-500 mt-3 text-center">
        Show to pharmacist on arrival
      </p>
    </div>
  )
}
