"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import {writeMarketHistory} from "./market-history";
let dialogSequence = 0;
const dialogStack: string[] = [];
let pendingCloseNavigation=0;
export function consumeDialogCloseNavigation(){if(pendingCloseNavigation>0){pendingCloseNavigation--;return true;}return false;}

export default function Dialog({
  title,
  children,
  onClose,
  wide = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const dialog = ref.current;
    dialog?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const marker = String(++dialogSequence);
    let disposed = false,
      pushed = false,
      popped = false;
    // Replacing one dialog schedules history.back() in its cleanup. Wait for
    // that navigation before adding this dialog's entry, otherwise its popstate
    // would immediately close the replacement (settings -> text size).
    let waitingForClose = pendingCloseNavigation > 0;
    const push = () => {
      if (disposed) return;
      dialogStack.push(marker);
      writeMarketHistory(
        "push",
        location.href,
        { __marketDialog: marker },
      );
      pushed = true;
    };
    queueMicrotask(() => { if (!waitingForClose) push(); });
    const pop = () => {
      if (waitingForClose) {
        waitingForClose = false;
        queueMicrotask(push);
        return;
      }
      if (
        dialogStack.at(-1) === marker &&
        history.state?.__marketDialog !== marker
      ) {
        popped = true;
        close.current();
      }
    };
    window.addEventListener("popstate", pop);
    return () => {
      disposed = true;
      window.removeEventListener("popstate", pop);
      const index = dialogStack.indexOf(marker);
      if (index >= 0) dialogStack.splice(index, 1);
      if (pushed && !popped && history.state?.__marketDialog === marker){
        pendingCloseNavigation++;
        history.back();
      }
      dialog?.close();
      document.body.style.overflow = overflow;
      previous?.focus();
    };
  }, []);
  return (
    <dialog
      className={`mk-dialog ${wide ? "mk-dialog-wide" : ""}`}
      ref={ref}
      aria-label={title}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="mk-dialog-content">
        <header className="mk-dialog-head">
          <h2>{title}</h2>
          <button className="mk-icon" onClick={onClose} aria-label="닫기">
            <X size={20} />
          </button>
        </header>
        <div className="mk-dialog-body">{children}</div>
      </div>
    </dialog>
  );
}
