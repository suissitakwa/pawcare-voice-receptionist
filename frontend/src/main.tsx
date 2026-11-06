import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import StaffApp from "./StaffApp";
import SubscriberApp from "./SubscriberApp";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {window.location.pathname.startsWith("/staff") ? (
      <StaffApp />
    ) : window.location.pathname.startsWith("/get-started") ? (
      <SubscriberApp />
    ) : (
      <App />
    )}
  </StrictMode>,
);
