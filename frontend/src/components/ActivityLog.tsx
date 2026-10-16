import type { LogEntry } from '../workflow/state'
import { Panel } from './Panel'

const LEVEL_CLASS: Record<LogEntry['level'], string> = {
  info: 'log--info',
  success: 'log--success',
  warning: 'log--warning',
  error: 'log--error',
}

export function ActivityLog({ entries }: { entries: LogEntry[] }) {
  return (
    <Panel title="Activity log" subtitle="Meaningful workflow transitions and failure reasons">
      {entries.length === 0 ? (
        <p className="empty">No activity yet.</p>
      ) : (
        <ol className="log">
          {entries.map((entry) => (
            <li key={entry.id} className={`log__item ${LEVEL_CLASS[entry.level]}`}>
              <span className="log__time">{new Date(entry.at).toLocaleTimeString()}</span>
              <span className="log__message">{entry.message}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  )
}
