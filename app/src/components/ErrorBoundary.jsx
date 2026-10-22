import { Component } from "react";

// A screen that throws must not leave a blank window: say what happened, keep the message for a bug report, and offer a reload.
export default class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  componentDidCatch(error, info) {
    console.error("Screen crashed:", error, info?.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div role="alert" style={{ padding: "64px 24px", maxWidth: 560, margin: "0 auto", fontFamily: "var(--font-ui)", color: "var(--fg-0)" }}>
        <h1 style={{ fontSize: 28, letterSpacing: "-0.03em", margin: "0 0 8px" }}>Something went wrong on this screen.</h1>
        <p style={{ color: "var(--fg-1)", lineHeight: 1.5 }}>Your projects and footage are untouched. Reload to carry on. If it happens again, send this line to whoever is fixing the app:</p>
        <pre style={{ whiteSpace: "pre-wrap", background: "var(--bg-2)", padding: 12, borderRadius: 8, fontSize: 12, color: "var(--fg-1)" }}>{String(this.state.error?.stack || this.state.error).slice(0, 600)}</pre>
        <button className="btn btn-primary" onClick={() => window.location.reload()}>Reload</button>
      </div>
    );
  }
}
