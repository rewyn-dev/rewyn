"use client";

/**
 * The execution graph (UI spec §10).
 *
 * A DAG for graph runs, a tree for multi-agent runs, a chain for everything
 * else. Nodes are clickable: selecting one scrolls the timeline to the event
 * it came from, which is how the two panels stay one investigation.
 */

import { useMemo } from "react";

import type { GraphEdgeView, GraphNodeView, GraphView } from "@/api/types";
import { useApi } from "@/hooks/useApi";
import { duration } from "@/lib/format";
import { Empty, Problem, Skeleton } from "@/components/common/atoms";

const NODE_WIDTH = 168;
const NODE_HEIGHT = 46;
const GAP_X = 66;
const GAP_Y = 28;

interface Placed {
  node: GraphNodeView;
  x: number;
  y: number;
}

const TONE: Record<string, string> = {
  ok: "var(--ok)",
  error: "var(--fail)",
  running: "var(--running)",
  skipped: "var(--text-faint)",
};

function layout(view: GraphView): { placed: Placed[]; width: number; height: number } {
  if (view.shape === "agents") {
    // Tree: the root on the left, everything it spawned to its right.
    const children = new Map<string, GraphNodeView[]>();
    const roots: GraphNodeView[] = [];
    for (const node of view.nodes) {
      if (node.parent && node.parent !== node.id) {
        children.set(node.parent, [...(children.get(node.parent) ?? []), node]);
      } else {
        roots.push(node);
      }
    }
    const placed: Placed[] = [];
    let row = 0;
    const walk = (node: GraphNodeView, depth: number) => {
      placed.push({ node, x: depth * (NODE_WIDTH + GAP_X), y: row * (NODE_HEIGHT + GAP_Y) });
      row += 1;
      for (const child of children.get(node.id) ?? []) walk(child, depth + 1);
    };
    for (const root of roots) walk(root, 0);
    const depth = Math.max(...placed.map((p) => p.x / (NODE_WIDTH + GAP_X)), 0);
    return {
      placed,
      width: (depth + 1) * (NODE_WIDTH + GAP_X),
      height: Math.max(row, 1) * (NODE_HEIGHT + GAP_Y),
    };
  }
  // Chain and DAG both read top to bottom: one node per step.
  const placed = view.nodes.map((node, index) => ({
    node,
    x: 0,
    y: index * (NODE_HEIGHT + GAP_Y),
  }));
  return {
    placed,
    width: NODE_WIDTH + 40,
    height: Math.max(view.nodes.length, 1) * (NODE_HEIGHT + GAP_Y),
  };
}

function edgePath(from: Placed, to: Placed): string {
  const start = { x: from.x + NODE_WIDTH / 2, y: from.y + NODE_HEIGHT };
  const end = { x: to.x + NODE_WIDTH / 2, y: to.y };
  if (from.x !== to.x) {
    const sideStart = { x: from.x + NODE_WIDTH, y: from.y + NODE_HEIGHT / 2 };
    const sideEnd = { x: to.x, y: to.y + NODE_HEIGHT / 2 };
    const mid = (sideStart.x + sideEnd.x) / 2;
    return `M ${sideStart.x} ${sideStart.y} C ${mid} ${sideStart.y}, ${mid} ${sideEnd.y}, ${sideEnd.x} ${sideEnd.y}`;
  }
  return `M ${start.x} ${start.y} L ${end.x} ${end.y}`;
}

export function ExecutionGraph({
  runId,
  onSelect,
}: {
  runId: string;
  onSelect?: (node: GraphNodeView) => void;
}) {
  const { data, problem, loading, reload } = useApi<GraphView>(`/runs/${runId}/graph`);
  const geometry = useMemo(() => (data ? layout(data) : null), [data]);

  if (problem) return <Problem problem={problem} onRetry={reload} />;
  if (loading && !data) return <Skeleton rows={6} />;
  if (!data || !geometry || data.nodes.length === 0) {
    return <Empty title="No graph">This run executed as a single step.</Empty>;
  }

  const index = new Map(geometry.placed.map((p) => [p.node.id, p]));
  const edges: [GraphEdgeView, Placed, Placed][] = data.edges.flatMap((edge) => {
    const from = index.get(edge.source);
    const to = index.get(edge.target);
    return from && to ? [[edge, from, to] as [GraphEdgeView, Placed, Placed]] : [];
  });

  return (
    <div className="graph-wrap">
      <svg
        width={geometry.width + 24}
        height={geometry.height + 12}
        role="group"
        aria-label={`Execution graph with ${data.nodes.length} nodes`}
      >
        <defs>
          <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" fill="var(--border-strong)" />
          </marker>
        </defs>
        {edges.map(([edge, from, to]) => (
          <g key={`${edge.source}->${edge.target}:${edge.kind}`}>
            <path
              d={edgePath(from, to)}
              fill="none"
              stroke={edge.kind === "handoff" ? "var(--accent)" : "var(--border-strong)"}
              strokeWidth={1.5}
              strokeDasharray={edge.kind === "spawn" ? "4 3" : undefined}
              markerEnd="url(#arrow)"
            />
            {edge.label ? (
              <text
                x={(from.x + to.x) / 2 + NODE_WIDTH / 2}
                y={(from.y + to.y) / 2 + NODE_HEIGHT / 2}
                fill="var(--text-faint)"
                fontSize="10"
              >
                {edge.label}
              </text>
            ) : null}
          </g>
        ))}
        {geometry.placed.map(({ node, x, y }) => (
          <g
            key={node.id}
            className="graph-node"
            transform={`translate(${x + 8} ${y + 4})`}
            onClick={() => onSelect?.(node)}
            tabIndex={0}
            role="button"
            aria-label={`${node.label}, ${node.status}`}
            onKeyDown={(event) => {
              if (event.key === "Enter") onSelect?.(node);
            }}
          >
            <rect
              width={NODE_WIDTH}
              height={NODE_HEIGHT}
              rx={6}
              fill="var(--bg-raised)"
              stroke={TONE[node.status] ?? "var(--border-strong)"}
              strokeWidth={1.4}
            />
            <text x={12} y={20}>
              {node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label}
            </text>
            <text className="sub" x={12} y={35}>
              {node.kind}
              {node.duration_ms ? ` · ${duration(node.duration_ms)}` : ""}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
