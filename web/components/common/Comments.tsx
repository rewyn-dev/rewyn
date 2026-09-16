"use client";

/**
 * The discussion on a run, an incident, a dataset or an agent (UI spec §45).
 *
 * One component, every subject. A thread is the only thing in the console
 * that a person wrote rather than an execution produced, so it is the only
 * thing that can be wrong in a way no amount of re-reading the recording
 * would fix -- which is why each note carries its author and its time, and
 * why resolving one keeps it in place rather than deleting it.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, get, post } from "@/api/client";
import type { IncidentComment, ProblemDetail } from "@/api/types";
import { ago } from "@/lib/format";
import { Problem } from "./atoms";

export function Comments({
  subject,
  title = "Discussion",
  onChange,
}: {
  subject: string;
  title?: string;
  onChange?: () => void;
}) {
  const [thread, setThread] = useState<IncidentComment[] | null>(null);
  const [problem, setProblem] = useState<ProblemDetail | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    get<IncidentComment[]>("/comments", { subject })
      .then(setThread)
      .catch((error: unknown) => {
        if (error instanceof ApiError) setProblem(error.problem);
      });
  }, [subject]);

  useEffect(load, [load]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const body = draft.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      const created = await post<IncidentComment>("/comments", { subject, body });
      setThread((current) => [...(current ?? []), created]);
      setDraft("");
      setProblem(null);
      onChange?.();
    } catch (error: unknown) {
      if (error instanceof ApiError) setProblem(error.problem);
    } finally {
      setBusy(false);
    }
  };

  const resolve = async (comment: IncidentComment) => {
    const updated = await post<IncidentComment>(
      `/comments/${comment.id}/resolve?resolved=${!comment.resolved}`,
      null,
    );
    setThread((current) => (current ?? []).map((c) => (c.id === updated.id ? updated : c)));
  };

  return (
    <section className="card" aria-labelledby={`comments-${subject}`}>
      <h2 className="section-title" id={`comments-${subject}`} style={{ marginTop: 0 }}>
        {title}
        {thread && thread.length > 0 ? <span className="count"> {thread.length}</span> : null}
      </h2>

      {problem ? <Problem problem={problem} /> : null}

      {thread && thread.length === 0 ? (
        <p style={{ color: "var(--text-faint)", margin: "0 0 10px" }}>
          Nothing written here yet. A note is the one thing the recording cannot tell the next
          person.
        </p>
      ) : null}

      {(thread ?? []).map((comment) => (
        <div className="comment" key={comment.id} data-resolved={comment.resolved}>
          <div className="comment-head">
            <strong>{comment.author}</strong>
            <span style={{ color: "var(--text-faint)" }}>{ago(comment.created_at)}</span>
            <span className="spacer" />
            <button className="btn btn-ghost" type="button" onClick={() => void resolve(comment)}>
              {comment.resolved ? "Reopen" : "Resolve"}
            </button>
          </div>
          <p style={{ margin: 0, whiteSpace: "pre-wrap" }}>{comment.body}</p>
        </div>
      ))}

      <form onSubmit={submit} style={{ marginTop: 10, display: "flex", gap: 8 }}>
        <label className="visually-hidden" htmlFor={`comment-${subject}`}>
          Add a note
        </label>
        <input
          id={`comment-${subject}`}
          className="input"
          style={{ flex: 1 }}
          placeholder="Add a note…"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
        />
        <button className="btn btn-primary" type="submit" disabled={busy || !draft.trim()}>
          Comment
        </button>
      </form>
    </section>
  );
}
