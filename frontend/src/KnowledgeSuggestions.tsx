import { useState } from "react";
import { ApiRequestError, request } from "./api";
import type { FaqItem, Profile } from "./subscriberApi";

/** An answer drafted from one of the owner's pages or documents, with the words it rests on. */
export interface SuggestedAnswer {
  question: string;
  answer: string;
  quote: string;
}

export interface Suggestions {
  source: string;
  kind: "page" | "document";
  answers: SuggestedAnswer[];
  /** Drafted answers left out because their quote isn't in the source. */
  dropped: number;
}

type Source = { url: string } | { document_id: string };

export function suggestAnswers(token: string, source: Source): Promise<Suggestions> {
  return request<Suggestions>("/api/v1/subscribers/profile/knowledge/suggest", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(source),
  });
}

interface Found extends SuggestedAnswer {
  source: string;
}

// These stop the whole run rather than one source.
const STOPPING = new Set(["suggestions_unavailable", "too_many_requests", "not_authenticated"]);

const same = (text: string) => text.trim().toLowerCase();

/**
 * Reads the owner's saved pages and documents one at a time and shows the answers drafted
 * from them. Nothing reaches the receptionist until the owner adds an answer, which then
 * joins their questions and answers like one they typed.
 */
export function KnowledgeSuggestions({
  token,
  faqs,
  prepare,
  onAdd,
  onSignedOut,
}: {
  token: string;
  faqs: FaqItem[];
  /** Saves unsaved edits and returns the profile, since only saved pages are read. */
  prepare: () => Promise<Profile>;
  onAdd: (faq: FaqItem) => void;
  onSignedOut: (err: unknown) => void;
}) {
  const [progress, setProgress] = useState("");
  const [found, setFound] = useState<Found[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [finished, setFinished] = useState("");

  const known = new Set(faqs.map((f) => same(f.question)));
  const waiting = found.filter((f) => !known.has(same(f.question)));

  async function start() {
    setFound([]);
    setProblems([]);
    setFinished("");
    setProgress("Saving your changes…");
    const issues: string[] = [];
    const answers: Found[] = [];
    try {
      const profile = await prepare();
      const sources: { label: string; body: Source }[] = [
        ...profile.details.knowledge_urls.map((url) => ({ label: url, body: { url } })),
        ...profile.documents.map((doc) => ({ label: doc.filename, body: { document_id: doc.id } })),
      ];
      if (sources.length === 0) {
        issues.push("Add a web page or upload a document first.");
        return;
      }
      for (const [index, source] of sources.entries()) {
        setProgress(`Reading ${index + 1} of ${sources.length}: ${source.label}…`);
        try {
          const result = await suggestAnswers(token, source.body);
          for (const answer of result.answers) {
            if (!answers.some((a) => same(a.question) === same(answer.question))) answers.push({ ...answer, source: source.label });
          }
          setFound([...answers]);
        } catch (err) {
          if (err instanceof ApiRequestError && err.status === 401) {
            onSignedOut(err);
            return;
          }
          const message = err instanceof ApiRequestError ? err.message : "Something went wrong.";
          if (err instanceof ApiRequestError && STOPPING.has(err.code)) {
            issues.push(message);
            if (answers.length === 0) return;
            break;
          }
          issues.push(`${source.label}: ${message}`);
        }
      }
      setFinished(
        answers.length === 0
          ? "No answers were found that your pages and documents state clearly."
          : `Found ${answers.length} possible ${answers.length === 1 ? "answer" : "answers"}. Check each one before adding it.`,
      );
    } catch (err) {
      if (err instanceof ApiRequestError && err.status === 401) onSignedOut(err);
      else issues.push(err instanceof ApiRequestError ? err.message : "Something went wrong.");
    } finally {
      setProblems(issues);
      setProgress("");
    }
  }

  const skip = (item: Found) => setFound((all) => all.filter((f) => f !== item));

  return (
    <div className="field">
      <span className="field-label">Suggested answers</span>
      <p className="field-hint">
        We can read your web pages and documents and suggest answers for you to check. Your receptionist only uses the answers you add, which are saved with this step.
      </p>
      <button type="button" className="button button-secondary" disabled={progress !== ""} onClick={() => void start()}>
        Suggest answers from my pages and documents
      </button>
      {progress && (
        <p className="muted" role="status">
          {progress}
        </p>
      )}
      {finished && !progress && (
        <p className="form-info" role="status">
          {finished}
        </p>
      )}
      {problems.length > 0 && (
        <ul className="form-alert" role="alert">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
      {waiting.length > 0 && (
        <ul className="block-list">
          {waiting.map((item) => (
            <li key={`${item.source}|${item.question}`} className="block-form">
              <p>
                <strong>{item.question}</strong>
              </p>
              <p>{item.answer}</p>
              <p className="field-hint">
                From {item.source}: “{item.quote}”
              </p>
              <div className="inline-actions">
                <button type="button" className="button button-secondary" onClick={() => onAdd({ question: item.question, answer: item.answer })}>
                  Add “{item.question}”
                </button>
                <button type="button" className="link-button" onClick={() => skip(item)}>
                  Skip “{item.question}”
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
