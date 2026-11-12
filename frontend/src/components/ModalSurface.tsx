import { useEffect, useRef, type ReactNode } from 'react';

/** Native modal surface for flows that supply their own title and footer. */
export function ModalSurface({label,busy,onClose,children,className=''}:{label:string;busy:boolean;onClose:()=>void;children:ReactNode;className?:string}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(()=>{const node=ref.current;node?.showModal();return()=>{if(node?.open)node.close()}},[]);
  return <dialog ref={ref} aria-label={label} className={`dify-modal dify-native-dialog ${className}`} onCancel={event=>{event.preventDefault();if(!busy)onClose()}}>{children}</dialog>;
}
