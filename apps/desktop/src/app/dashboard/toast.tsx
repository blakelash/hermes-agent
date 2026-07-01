import { useStore } from '@nanostores/react'

import { $toast } from '@/store/dashboard'

/** Bottom-center ink pill (design/v2 spec). Driven by the dashboard `$toast` atom. */
export function DashboardToast() {
  const toast = useStore($toast)

  if (!toast) {
    return null
  }

  return (
    <div className="hd-toast" role="status">
      <span className="hd-toast-check">✓</span>
      {toast}
    </div>
  )
}
