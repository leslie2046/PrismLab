import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';
import { Inbox } from 'lucide-react';

export function EmptyState({title, description, icon: Icon = Inbox, action, compact = false}: {
  title: ReactNode; description?: ReactNode; icon?: LucideIcon; action?: ReactNode; compact?: boolean;
}) {
  return <div className={`ui-empty-state ${compact ? 'is-compact' : ''}`}><span className="ui-empty-icon"><Icon aria-hidden="true" /></span><strong>{title}</strong>{description && <p>{description}</p>}{action && <div>{action}</div>}</div>;
}
