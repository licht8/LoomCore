import { describe, expect, it } from "vitest"

import type { ChatMessage, Plan, PlanStep, SessionMessage } from "@/lib/chat-types"
import {
  appendAssistantDelta,
  applyPlanStep,
  finalizeAssistant,
  hydrateMessages,
  upsertPlanMessage,
} from "@/lib/chat-hydrate"

function assistant(id: string, content = ""): ChatMessage {
  return { id, role: "assistant", content, timestamp: "10:00" }
}

function step(overrides: Partial<PlanStep> = {}): PlanStep {
  return {
    id: "s1",
    tool: "core_health",
    args: {},
    why: "check",
    risk: "safe",
    status: "pending",
    ...overrides,
  }
}

function plan(overrides: Partial<Plan> = {}): Plan {
  return {
    type: "plan",
    plan_id: "pl_1",
    revision: 1,
    goal: "Check health",
    steps: [step()],
    status: "proposed",
    created_at: "2026-09-28T10:00:00Z",
    ...overrides,
  }
}

describe("appendAssistantDelta", () => {
  it("appends a fragment to an existing bubble", () => {
    const out = appendAssistantDelta([assistant("m1", "Hel")], "m1", "lo")
    expect(out[0].content).toBe("Hello")
    expect(out).toHaveLength(1)
  })

  it("creates a bubble when the id is new", () => {
    const out = appendAssistantDelta([], "m1", "Hi")
    expect(out).toHaveLength(1)
    expect(out[0]).toMatchObject({ id: "m1", role: "assistant", content: "Hi" })
  })

  it("replaces content when reset is true", () => {
    const out = appendAssistantDelta([assistant("m1", "old")], "m1", "new", true)
    expect(out[0].content).toBe("new")
  })

  it("is a no-op for an empty reset with no bubble", () => {
    const messages: ChatMessage[] = []
    expect(appendAssistantDelta(messages, "m1", "", true)).toBe(messages)
  })
})

describe("finalizeAssistant", () => {
  it("replaces the streamed text", () => {
    const out = finalizeAssistant([assistant("m1", "partial")], "m1", "final")
    expect(out[0].content).toBe("final")
  })
})

describe("upsertPlanMessage", () => {
  it("inserts then replaces a plan by plan_id", () => {
    const first = upsertPlanMessage([], plan())
    expect(first).toHaveLength(1)
    expect(first[0]).toMatchObject({ role: "plan", content: "Check health" })

    const second = upsertPlanMessage(first, plan({ revision: 2, goal: "Updated" }))
    expect(second).toHaveLength(1)
    expect(second[0].plan?.revision).toBe(2)
    expect(second[0].content).toBe("Updated")
    expect(second[0].id).toBe(first[0].id)
  })
})

describe("applyPlanStep", () => {
  it("merges a known step and updates the plan status", () => {
    const out = applyPlanStep(
      upsertPlanMessage([], plan()),
      "pl_1",
      step({ status: "done" }),
      "done",
    )
    expect(out[0].plan?.steps[0].status).toBe("done")
    expect(out[0].plan?.status).toBe("done")
  })

  it("appends an unknown step id", () => {
    const out = applyPlanStep(
      upsertPlanMessage([], plan()),
      "pl_1",
      step({ id: "s2", tool: "core_list_plugins" }),
    )
    expect(out[0].plan?.steps).toHaveLength(2)
  })

  it("leaves unrelated messages untouched", () => {
    const messages = [assistant("m1", "hi")]
    expect(applyPlanStep(messages, "pl_1", step())[0]).toBe(messages[0])
  })
})

describe("hydrateMessages", () => {
  it("maps user, assistant and tool rows", () => {
    const rows: SessionMessage[] = [
      { id: "1", role: "user", content: "hi", created_at: "2026-09-28T10:00:00Z" },
      { id: "2", role: "assistant", content: "hello", created_at: "2026-09-28T10:00:01Z" },
      {
        id: "3",
        role: "tool",
        content: "out",
        created_at: "2026-09-28T10:00:02Z",
        tool_calls: [{ name: "core_health", status: "success" }],
        tool_result: "raw",
      },
    ]
    const out = hydrateMessages(rows)
    expect(out.map((m) => m.role)).toEqual(["user", "assistant", "tool"])
    expect(out[2]).toMatchObject({ tool: "core_health", toolCode: "raw" })
  })

  it("skips a leaked planner tool name row", () => {
    const rows: SessionMessage[] = [
      { id: "1", role: "assistant", content: "reply", created_at: "2026-09-28T10:00:00Z" },
    ]
    expect(hydrateMessages(rows)).toEqual([])
  })
})
