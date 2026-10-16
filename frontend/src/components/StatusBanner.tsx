import type { StatusDescriptor } from '../workflow/state'
import { StatusPill } from './StatusPill'

interface StatusBannerProps {
  descriptor: StatusDescriptor
  errorCode?: string
  onDismiss?: () => void
}

export function StatusBanner({ descriptor, errorCode, onDismiss }: StatusBannerProps) {
  return (
    <div className={`banner banner--${descriptor.tone}`} role="status">
      <div className="banner__main">
        <StatusPill tone={descriptor.tone}>{descriptor.label}</StatusPill>
        <span className="banner__detail">{descriptor.detail}</span>
        {errorCode ? <code className="banner__code">{errorCode}</code> : null}
      </div>
      {onDismiss ? (
        <button type="button" className="btn btn--ghost" onClick={onDismiss}>
          Dismiss
        </button>
      ) : null}
    </div>
  )
}
