"use client";

/**
 * The first screen (UI spec §47, §59, §61).
 *
 * Every other screen in this console answers a question about runs that
 * already exist. This one answers the question somebody has before any do:
 * what is this, and what do I do?
 *
 * Three things, in the order a person needs them. What the tool is, in one
 * paragraph. A path from nothing to a console worth opening, with each step
 * ticked from what the project actually contains — a checklist that fills
 * itself in, not a tutorial you have to keep your place in. And the four
 * jobs the thing is for, each linking to the screen that does it, each
 * honest about what it still needs.
 *
 * The demo button is the important one. An inspection tool is worth nothing
 * until you have recorded something, and you cannot tell whether recording is
 * worth it until you have seen the tool. Loading a demo breaks that circle,
 * and because every demo run is tagged, removing it is exact.
 */

import { useState } from "react";

import { ApiError, del, post } from "@/api/client";
import type { Onboarding, OnboardingStep, ProblemDetail, UseCase } from "@/api/types";
import { Code, Empty, Problem, Row, Skeleton, Tag } from "@/components/common/atoms";
import { useApi } from "@/hooks/useApi";
import { Link } from "@/lib/router";

function Step({ step, index }: { step: OnboardingStep; index: number }) {
  return (
    <li className="step" data-done={step.done}>
      <span className="step-mark" aria-hidden="true">
        {step.done ? "✓" : index + 1}
      </span>
      <div className="step-body">
        <Row gap={8}>
          <strong>{step.title}</strong>
          {step.done ? <Tag tone="ok">done</Tag> : null}
        </Row>
        <p className="step-detail">{step.detail}</p>
        {step.code ? <Code label={step.title}>{step.code}</Code> : null}
        {step.href && step.action ? (
          <Link className="btn btn-ghost" href={step.href}>
            {step.action}
          </Link>
        ) : null}
      </div>
    </li>
  );
}

function Job({ job }: { job: UseCase }) {
  return (
    <Link className="job" href={job.href} data-ready={job.ready}>
      <div className="job-title">{job.title}</div>
      <div className="job-question">{job.question}</div>
      <p className="job-detail">{job.detail}</p>
      {job.needs ? <div className="job-needs">Needs: {job.needs}</div> : null}
    </Link>
  );
}

export function WelcomeScreen() {
  const { data, problem, loading, reload } = useApi<Onboarding>("/onboarding");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<ProblemDetail | null>(null);

  if (problem)
    return (
      <div className="page">
        <Problem problem={problem} />
      </div>
    );
  if (loading && !data)
    return (
      <div className="page">
        <Skeleton rows={8} />
      </div>
    );
  if (!data) return null;

  const act = async (run: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await run();
      setFailure(null);
      reload();
      // Every other screen caches its own document; a project that just
      // gained or lost its runs invalidates all of them.
      window.location.reload();
    } catch (error: unknown) {
      if (error instanceof ApiError) setFailure(error.problem);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page page-narrow">
      <h1 className="page-title">Rewyn</h1>
      <p className="welcome-summary">{data.summary}</p>

      <section className="card" aria-labelledby="where">
        <h2 className="section-title" id="where" style={{ marginTop: 0 }}>
          Where you are
        </h2>
        <Row gap={8}>
          <Tag tone="accent">{data.location.surface}</Tag>
          <strong>{data.location.project}</strong>
          {data.location.home ? <span className="mono">{data.location.home}</span> : null}
          {data.location.endpoint && !data.location.home ? (
            <span className="mono">{data.location.endpoint}</span>
          ) : null}
        </Row>
        <p style={{ color: "var(--text-muted)", marginBottom: 0 }}>{data.location.how_to_change}</p>
      </section>

      {failure ? <Problem problem={failure} /> : null}

      {data.can_load_demo ? (
        <section className="card" aria-labelledby="demo">
          <h2 className="section-title" id="demo" style={{ marginTop: 0 }}>
            {data.demo_loaded ? "Demo data is loaded" : "Not ready to instrument anything yet?"}
          </h2>
          <p style={{ color: "var(--text-muted)" }}>
            {data.demo_loaded
              ? "This project is holding a demo: two agents, two versions, a failure cluster, an evaluation and a dataset. Removing it deletes those runs and nothing else."
              : "Load a small demo project — two agents across two versions, a cluster of identical failures, an evaluation and a dataset — and every screen has something real to show. It records locally with no API key, and it comes out cleanly."}
          </p>
          <Row>
            {data.demo_loaded ? (
              <button
                className="btn"
                type="button"
                disabled={busy}
                onClick={() => void act(() => del("/demo"))}
              >
                {busy ? "Removing…" : "Remove the demo data"}
              </button>
            ) : (
              <button
                className="btn btn-primary"
                type="button"
                disabled={busy}
                onClick={() => void act(() => post("/demo", null))}
              >
                {busy ? "Loading…" : "Load the demo project"}
              </button>
            )}
            {data.counts.runs > 0 ? (
              <Link className="btn btn-ghost" href="/runs">
                Open runs
              </Link>
            ) : null}
          </Row>
        </section>
      ) : null}

      <h2 className="section-title">Getting there</h2>
      <ol className="steps-list">
        {data.steps.map((step, index) => (
          <Step key={step.key} step={step} index={index} />
        ))}
      </ol>

      <h2 className="section-title">What this is for</h2>
      <div className="jobs">
        {data.jobs.map((job) => (
          <Job key={job.key} job={job} />
        ))}
      </div>

      {data.empty ? (
        <Empty title="Nothing recorded yet">
          <p>
            Every screen in this console is derived from runs, so they fill in as runs arrive.
            Nothing here is configured, and nothing is created in the UI.
          </p>
        </Empty>
      ) : null}
    </div>
  );
}
