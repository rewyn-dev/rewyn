"use client";

/**
 * The console (UI spec §62).
 *
 * One shell, one client-side router, one screen at a time. Routes that are
 * not built yet resolve to the teaching empty state rather than a dead end.
 */

import { AppShell } from "./AppShell";
import { NotBuilt } from "./NotBuilt";
import { OverviewScreen } from "./Overview";
import { EnvironmentsScreen, SessionsScreen, SettingsScreen } from "./SimpleScreens";
import { findEntry } from "./nav";
import { Empty } from "./atoms";
import { AgentScreen, AgentsScreen } from "@/components/agents/AgentScreens";
import {
  REGISTRY_PAGES,
  RegistryDetailScreen,
  RegistryScreen,
} from "@/components/agents/RegistryScreens";
import { DatasetScreen, DatasetsScreen } from "@/components/datasets/DatasetScreens";
import {
  EvaluationsScreen,
  RegressionReportScreen,
  RegressionScreen,
} from "@/components/evaluations/QualityScreens";
import {
  CostScreen,
  DependenciesScreen,
  DriftScreen,
  ExperimentsScreen,
  NotificationsScreen,
  ReleasesScreen,
} from "@/components/intelligence/IntelligenceScreens";
import { IncidentScreen, IncidentsScreen } from "@/components/incidents/IncidentScreens";
import { LiveScreen } from "@/components/live/LiveScreens";
import { WelcomeScreen } from "@/components/onboarding/Welcome";
import { CompareWorkspace } from "@/components/replay/CompareWorkspace";
import { ReplayWorkspace } from "@/components/replay/ReplayWorkspace";
import { RunScreen } from "@/components/runs/RunScreen";
import { RunsScreen } from "@/components/runs/RunsScreen";
import { WorkspaceScreen } from "@/components/workspace/Workspace";
import { Link, segments, useRouter } from "@/lib/router";

export function Console() {
  const { path, params } = useRouter();
  const parts = segments(path);

  return (
    <AppShell>
      {({ capabilities, environment }) => {
        if (parts.length === 0) {
          // A project with nothing in it has no health to report, so the
          // overview would be five zeros and three empty lists. Answer the
          // question a new reader actually has instead (UI §47).
          return capabilities && capabilities.counts.runs === 0 ? (
            <WelcomeScreen />
          ) : (
            <OverviewScreen environment={environment} />
          );
        }
        if (parts[0] === "welcome") return <WelcomeScreen />;
        if (parts[0] === "runs") {
          if (!parts[1]) return <RunsScreen environment={environment} />;
          if (parts[2] === "replay") return <ReplayWorkspace runId={parts[1]} key={parts[1]} />;
          return <RunScreen runId={parts[1]} key={parts[1]} />;
        }
        if (parts[0] === "compare") {
          return <CompareWorkspace a={params.get("a")} b={params.get("b")} />;
        }
        if (parts[0] === "replay") {
          return <ReplayPicker />;
        }
        if (parts[0] === "datasets") {
          return parts[1] ? <DatasetScreen name={parts[1]} key={parts[1]} /> : <DatasetsScreen />;
        }
        if (parts[0] === "agents") {
          return parts[1] ? <AgentScreen name={parts[1]} key={parts[1]} /> : <AgentsScreen />;
        }
        if (parts[0] === "evaluations") return <EvaluationsScreen />;
        if (parts[0] === "live") return <LiveScreen capabilities={capabilities} />;
        if (parts[0] === "dependencies") return <DependenciesScreen />;
        if (parts[0] === "drift") return <DriftScreen />;
        if (parts[0] === "cost") return <CostScreen />;
        if (parts[0] === "releases") return <ReleasesScreen />;
        if (parts[0] === "experiments") return <ExperimentsScreen />;
        if (parts[0] === "notifications") return <NotificationsScreen />;
        if (parts[0] === "incidents") {
          return parts[1] ? <IncidentScreen id={parts[1]} key={parts[1]} /> : <IncidentsScreen />;
        }
        if (parts[0] === "workspace") return <WorkspaceScreen agent={params.get("agent")} />;
        if (parts[0] === "regression") {
          return parts[1] ? (
            <RegressionReportScreen id={parts[1]} key={parts[1]} />
          ) : (
            <RegressionScreen />
          );
        }
        if (parts[0] && parts[0] in REGISTRY_PAGES) {
          return parts[1] ? (
            <RegistryDetailScreen
              page={parts[0]}
              name={decodeURIComponent(parts[1])}
              key={parts[1]}
            />
          ) : (
            <RegistryScreen page={parts[0]} key={parts[0]} />
          );
        }
        if (parts[0] === "sessions") return <SessionsScreen />;
        if (parts[0] === "environments") return <EnvironmentsScreen />;
        if (parts[0] === "settings") return <SettingsScreen capabilities={capabilities} />;

        const entry = findEntry(`/${parts[0]}`);
        if (entry) return <NotBuilt entry={entry} />;
        return (
          <div className="page">
            <Empty title="No such screen">
              <p>
                <span className="mono">{path}</span> is not part of the console.
              </p>
              <Link className="btn" href="/">
                Back to the overview
              </Link>
            </Empty>
          </div>
        );
      }}
    </AppShell>
  );
}

/** Replay starts from a run, so the nav entry says where to start (UI §47). */
function ReplayPicker() {
  return (
    <div className="page">
      <h1 className="page-title">Replay</h1>
      <Empty title="Open a run first">
        <p>
          A replay reproduces one recorded execution, so it starts from that run: open one and press{" "}
          <kbd>R</kbd>, or use the Replay button in its header.
        </p>
        <Link className="btn" href="/runs">
          Browse runs
        </Link>
      </Empty>
    </div>
  );
}
