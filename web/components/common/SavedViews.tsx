"use client";

/**
 * Filters a team kept, so a question asked once can be asked again (UI §45).
 *
 * The runs page can hold a dozen filters at once and the useful combinations
 * are rediscovered rather than remembered. A saved view is just that query
 * string with a name on it, which is why saving one needs no dialog: the
 * screen already knows what is on it.
 */

import { useCallback, useEffect, useState } from "react";

import { ApiError, del, get, post } from "@/api/client";
import type { SavedView } from "@/api/types";
import { Link, useRouter } from "@/lib/router";
import { Row } from "./atoms";

export function SavedViews({ screen }: { screen: string }) {
  const { params, navigate } = useRouter();
  const [views, setViews] = useState<SavedView[]>([]);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");

  const load = useCallback(() => {
    get<SavedView[]>("/views", { screen })
      .then(setViews)
      .catch(() => setViews([]));
  }, [screen]);

  useEffect(load, [load]);

  const current = params.toString();

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    try {
      const created = await post<SavedView>("/views", {
        name: name.trim(),
        screen,
        query: current ? `?${current}` : "",
      });
      setViews((all) => [...all, created].sort((a, b) => a.name.localeCompare(b.name)));
    } catch (error: unknown) {
      if (!(error instanceof ApiError)) throw error;
    }
    setName("");
    setNaming(false);
  };

  const remove = async (view: SavedView) => {
    await del(`/views/${view.id}`);
    setViews((all) => all.filter((v) => v.id !== view.id));
  };

  if (views.length === 0 && !naming && !current) return null;

  return (
    <Row gap={6}>
      {views.map((view) => (
        <span className="saved-view" key={view.id}>
          <Link
            href={`/${view.screen}${view.query}`}
            title={view.description || `Saved by ${view.author}`}
          >
            {view.name}
          </Link>
          <button
            className="saved-view-remove"
            type="button"
            aria-label={`Remove the saved view ${view.name}`}
            onClick={() => void remove(view)}
          >
            ×
          </button>
        </span>
      ))}
      {naming ? (
        <form onSubmit={save} style={{ display: "flex", gap: 6 }}>
          <label className="visually-hidden" htmlFor="saved-view-name">
            Name this view
          </label>
          <input
            id="saved-view-name"
            className="input"
            autoFocus
            placeholder="Name this view…"
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
          <button className="btn btn-primary" type="submit" disabled={!name.trim()}>
            Save
          </button>
          <button className="btn btn-ghost" type="button" onClick={() => setNaming(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <button
          className="btn btn-ghost"
          type="button"
          onClick={() => setNaming(true)}
          disabled={!current}
          title={current ? "Keep this filter" : "Filter the list first, then save it"}
        >
          Save this view
        </button>
      )}
      {views.length > 0 && current ? (
        <button className="btn btn-ghost" type="button" onClick={() => navigate(`/${screen}`)}>
          Clear
        </button>
      ) : null}
    </Row>
  );
}
