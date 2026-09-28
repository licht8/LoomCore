import { describe, expect, it } from "vitest"

import type { UseChatState } from "@/lib/chat-types"
import { applyWsMessage } from "@/lib/chat-ws"

function baseState(overrides: Partial<UseChatState> = {}): UseChatState {
  return {
    messages: [],
    isThinking: false,
    planProgress: "",
    planRequest: null,
    error: null,
    isSending: false,
    wsReady: true,
    sessions: [],
    sessionsLoading: false,
    ...overrides,
  }
}

describe("applyWsMessage", () => {
  it("thinking sets isThinking and clears the error", () => {
    const state = applyWsMessage(baseState({ error: "boom" }), { type: "thinking" })
    expect(state.isThinking).toBe(true)
    expect(state.error).toBeNull()
  })

  it("response without message_id appends one assistant message", () => {
    const state = applyWsMessage(baseState({ isThinking: true, isSending: true }), {
      type: "response",
      content: "hi",
    })
    expect(state.isThinking).toBe(false)
    expect(state.isSending).toBe(false)
    expect(state.messages).toHaveLength(1)
    expect(state.messages[0]).toMatchObject({ role: "assistant", content: "hi" })
  })

  it("response with message_id reconciles the streamed bubble", () => {
    const streamed = applyWsMessage(baseState(), {
      type: "delta",
      message_id: "m1",
      content: "par",
    })
    const final = applyWsMessage(streamed, {
      type: "response",
      message_id: "m1",
      content: "final",
    })
    expect(final.messages).toHaveLength(1)
    expect(final.messages[0].content).toBe("final")
  })

  it("delta appends fragments and reset replaces them", () => {
    let state = applyWsMessage(baseState(), { type: "delta", message_id: "m1", content: "a" })
    state = applyWsMessage(state, { type: "delta", message_id: "m1", content: "b" })
    expect(state.messages[0].content).toBe("ab")

    state = applyWsMessage(state, {
      type: "delta",
      message_id: "m1",
      content: "z",
      reset: true,
    })
    expect(state.messages[0].content).toBe("z")
    expect(state.isThinking).toBe(true)
  })

  it("delta without a message_id is ignored", () => {
    const state = baseState()
    expect(applyWsMessage(state, { type: "delta", content: "x" })).toBe(state)
  })

  it("tool_call appends a pending tool card", () => {
    const state = applyWsMessage(baseState(), { type: "tool_call", tool: "core_health" })
    expect(state.messages[0]).toMatchObject({
      role: "tool",
      tool: "core_health",
      toolStatus: "pending",
    })
    expect(state.isThinking).toBe(true)
  })

  it("error clears thinking and surfaces the message", () => {
    const state = applyWsMessage(baseState({ isThinking: true }), {
      type: "error",
      error: "nope",
    })
    expect(state.isThinking).toBe(false)
    expect(state.error).toBe("nope")
  })

  it("schema_warning is a no-op", () => {
    const state = baseState()
    expect(applyWsMessage(state, { type: "schema_warning", unavailable_plugins: ["x"] })).toBe(
      state,
    )
  })
})
