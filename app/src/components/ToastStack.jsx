/**
 * ToastStack — top-right notification overlay.
 *
 * Receives an array of toast objects from App state and renders them with
 * slide-in/fade-out animations. Each toast auto-dismisses from App's
 * setTimeout, so this component is purely presentational.
 */
export default function ToastStack({ toasts }) {
  if (!toasts.length) return null;
  return (
    <div className="toast-stack">
      {toasts.map((t) => (
        <div key={t.id} className={`toast toast-${t.level || "info"}`}>
          <span className="toast-dot" />
          <span className="toast-message">{t.message}</span>
        </div>
      ))}
    </div>
  );
}
