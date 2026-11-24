import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import AdminApp from "./AdminApp";
import App from "./App";
import StaffApp from "./StaffApp";
import SubscriberApp from "./SubscriberApp";
import { businessSlug, pagePath } from "./site";
import "./styles.css";

// /b/{slug}/... pages belong to that business; the same pages at the root serve the demo.
const page = pagePath();
const atRoot = businessSlug() === null;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {atRoot && page.startsWith("/admin") ? (
      <AdminApp />
    ) : page.startsWith("/staff") ? (
      <StaffApp />
    ) : atRoot && page.startsWith("/get-started") ? (
      <SubscriberApp />
    ) : (
      <App />
    )}
  </StrictMode>,
);
