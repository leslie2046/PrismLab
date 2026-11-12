import type { ReactNode } from 'react';
import type { LucideIcon } from 'lucide-react';

/** Shared page hierarchy: identity first, one task action, then local tools. */
export function PageHeader({ title, description, icon: Icon, actions, breadcrumb, count, meta, className = '' }: {
  title: ReactNode; description?: ReactNode; icon?: LucideIcon; actions?: ReactNode;
  breadcrumb?: ReactNode; count?: ReactNode; meta?: ReactNode; className?: string;
}) {
  return <header className={`page-header panel-head ${className}`}>
    <div className="page-header-identity">{breadcrumb && <div className="page-breadcrumb">{breadcrumb}</div>}
      <div className="page-header-title">{Icon && <span className="page-icon"><Icon aria-hidden="true" /></span>}<div><div className="page-title-line"><h2>{title}</h2>{count !== undefined && <span className="page-count">{count}</span>}</div>{description && <p>{description}</p>}{meta && <div className="page-header-meta">{meta}</div>}</div></div>
    </div>{actions && <div className="page-header-actions">{actions}</div>}
  </header>;
}
