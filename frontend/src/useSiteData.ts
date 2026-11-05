import { useCallback, useEffect, useState } from "react";
import { fetchSiteData, type SiteData } from "./api";

export type SiteDataState =
  | { status: "loading" }
  | { status: "error" }
  | { status: "ready"; data: SiteData };

export function useSiteData(): { state: SiteDataState; retry: () => void } {
  const [state, setState] = useState<SiteDataState>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetchSiteData(controller.signal)
      .then((data) => setState({ status: "ready", data }))
      .catch(() => {
        if (!controller.signal.aborted) setState({ status: "error" });
      });
    return () => controller.abort();
  }, [attempt]);

  const retry = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  return { state, retry };
}
