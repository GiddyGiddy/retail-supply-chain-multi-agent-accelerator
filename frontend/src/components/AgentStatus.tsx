import { useEffect, useState } from "react";
import type { AccountInfo } from "@azure/msal-browser";
import { apiRequest } from "../api";

interface AgentInfo {
  id: string;
  name: string;
  uses_llm: boolean;
  llm_mode: "always" | "optional" | "never";
  execution_mode: "llm" | "deterministic" | "deterministic_fallback";
  technology: string;
  responsibility: string;
}

interface AgentStatusProps {
  apiOnline: boolean | null;
  account: AccountInfo | null;
}

const modeLabel: Record<AgentInfo["execution_mode"], string> = {
  llm: "LLM active",
  deterministic: "Code only",
  deterministic_fallback: "LLM off · fallback",
};

export function AgentStatus({ apiOnline, account }: AgentStatusProps) {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!apiOnline) return;
    apiRequest<AgentInfo[]>("/api/agents", account)
      .then(setAgents)
      .catch((reason) => setError(reason instanceof Error ? reason.message : "Could not load agents."));
  }, [apiOnline, account]);

  return (
    <section className="card">
      <div className="section-heading">
        <div>
          <p className="eyebrow">SYSTEM STATUS</p>
          <h2>Agent operations</h2>
        </div>
        <span className={`status-pill ${apiOnline ? "online" : "offline"}`}>
          <span className="status-dot" />
          {apiOnline === null ? "Checking" : apiOnline ? "API online" : "API offline"}
        </span>
      </div>
      <p className="muted">Agent proposals require human approval before any downstream action.</p>
      {error && <p className="error" role="status">{error}</p>}
      {agents.length > 0 && (
        <ul className="agent-list" aria-label="Agent roster">
          {agents.map((agent) => (
            <li key={agent.id} className="agent-row">
              <div>
                <strong>{agent.name}</strong>
                <p className="muted">{agent.responsibility}</p>
                <p className="muted">{agent.technology}</p>
              </div>
              <span className={`status-pill ${agent.execution_mode === "llm" ? "online" : ""}`}>
                {modeLabel[agent.execution_mode]}
                {agent.llm_mode === "optional" ? " · LLM optional" : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
