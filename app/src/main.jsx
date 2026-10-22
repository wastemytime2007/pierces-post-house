import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";
import SettingsWindow from "./screens/SettingsWindow.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import "./styles.css";

// One bundle, two windows: the main one, and the Settings window macOS expects behind Command-comma.
const isSettings = new URLSearchParams(location.search).get("settings") === "1";

ReactDOM.createRoot(document.getElementById("root")).render(
  <ErrorBoundary>
    {isSettings ? <SettingsWindow /> : <App />}
  </ErrorBoundary>
);
