import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AlertTriangle, X } from 'lucide-react';

export type ConfirmOptions = {
  title: string;
  message: string;
  detail?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
};

type PendingConfirm = ConfirmOptions & { resolve: (value: boolean) => void };
type ConfirmContextValue = (options: ConfirmOptions) => Promise<boolean>;
const ConfirmContext = createContext<ConfirmContextValue | null>(null);

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const settle = useCallback((value: boolean) => {
    const current = pending;
    if (!current) return;
    setPending(null);
    if (dialog.current?.open) dialog.current.close();
    current.resolve(value);
  }, [pending]);
  const confirm = useCallback((options: ConfirmOptions) => new Promise<boolean>(resolve => {
    setPending({ ...options, resolve });
  }), []);
  useEffect(() => {
    if (pending && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [pending]);
  return <ConfirmContext.Provider value={confirm}>
    {children}
    <dialog ref={dialog} className={`dify-modal dify-native-dialog confirm-dialog${pending?.danger ? ' confirm-dialog-danger' : ''}`} aria-labelledby="confirm-dialog-title" onCancel={event => { event.preventDefault(); settle(false); }}>
      <div className="dify-modal-head confirm-dialog-head"><div><h2 id="confirm-dialog-title">{pending?.title}</h2><p>{pending?.message}</p></div><button type="button" className="dify-close" aria-label="关闭" onClick={() => settle(false)}><X aria-hidden="true" /></button></div>
      {pending?.detail && <div className="confirm-dialog-body"><AlertTriangle aria-hidden="true" /><p>{pending.detail}</p></div>}
      <div className="dify-modal-footer confirm-dialog-footer"><button type="button" className="dify-secondary-button" onClick={() => settle(false)}>{pending?.cancelLabel || '取消'}</button><button type="button" className={pending?.danger ? 'dify-danger-button' : 'dify-blue-button'} autoFocus onClick={() => settle(true)}>{pending?.confirmLabel || '确定'}</button></div>
    </dialog>
  </ConfirmContext.Provider>;
}

export function useConfirm() {
  const value = useContext(ConfirmContext);
  if (!value) throw new Error('useConfirm must be used inside ConfirmProvider');
  return value;
}
