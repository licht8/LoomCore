"use client"

import { useQuery } from "@tanstack/react-query"

import { fetchLlmSettings } from "@/lib/llm-settings-api"

export function useLlmSettings() {
  return useQuery({
    queryKey: ["llm-settings"],
    queryFn: fetchLlmSettings,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
  })
}
