import { useEffect, useRef } from "react";
import type { RefObject } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Focus management for modal dialogs that declare `aria-modal`:
 *  - on open, remembers the currently focused element and moves focus into the
 *    dialog (an element marked `data-autofocus`, else the first focusable one);
 *  - traps Tab / Shift+Tab inside the container so focus can't leak to the
 *    background content the dialog claims to have made inert;
 *  - on close / unmount, restores focus to wherever it was before opening;
 *  - optionally routes Escape to `onEscape`, so each modal no longer needs to
 *    register its own keydown effect.
 *
 * `onEscape` is read through a ref, so passing a fresh inline closure every
 * render does not re-run the trap (which would otherwise steal focus back to
 * the initial element on each parent re-render).
 */
export function useFocusTrap<T extends HTMLElement>(
  open: boolean,
  containerRef: RefObject<T | null>,
  onEscape?: () => void,
) {
  const escapeRef = useRef(onEscape);
  useEffect(() => {
    escapeRef.current = onEscape;
  });

  useEffect(() => {
    if (!open) return;
    const container = containerRef.current;
    const previouslyFocused = document.activeElement as HTMLElement | null;

    const focusable = () =>
      Array.from(container?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(
        (el) => el.offsetParent !== null || el === document.activeElement,
      );

    // Move focus into the dialog once it has mounted.
    const initial =
      container?.querySelector<HTMLElement>("[data-autofocus]") ?? focusable()[0] ?? container;
    initial?.focus();

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && escapeRef.current) {
        e.preventDefault();
        escapeRef.current();
        return;
      }
      if (e.key !== "Tab" || !container) return;
      const items = focusable();
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      } else if (active instanceof Node && !container.contains(active)) {
        // Focus somehow escaped the dialog — pull it back in.
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [open, containerRef]);
}
