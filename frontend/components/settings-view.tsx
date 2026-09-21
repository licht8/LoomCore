"use client"

import { useEffect, useMemo, useState } from "react"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  Check,
  KeyRound,
  Loader2,
  PlugZap,
  Save,
} from "lucide-react"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import {
  fetchLlmProviders,
  fetchLlmSettings,
  testLlmConnection,
  updateLlmSettings,
  type LlmSettings,
  type LlmSettingsUpdate,
  type LlmTestResult,
} from "@/lib/llm-settings-api"
import { cn } from "@/lib/utils"

type FormState = {
  provider: string
  base_url: string
  model: string
  summary_model: string
  enable_thinking: boolean
  thinking_template: boolean
  connect_timeout: string
  read_timeout: string
  write_timeout: string
  pool_timeout: string
  max_retries: string
}

function toForm(settings: LlmSettings): FormState {
  return {
    provider: settings.provider,
    base_url: settings.base_url,
    model: settings.model,
    summary_model: settings.summary_model,
    enable_thinking: settings.enable_thinking,
    thinking_template: settings.thinking_template,
    connect_timeout: String(settings.connect_timeout),
    read_timeout: String(settings.read_timeout),
    write_timeout: String(settings.write_timeout),
    pool_timeout: String(settings.pool_timeout),
    max_retries: String(settings.max_retries),
  }
}

function ErrorBanner({
  message,
  actionLabel,
  onAction,
}: {
  message: string
  actionLabel: string
  onAction: () => void
}) {
  return (
    <div
      role="alert"
      className="mt-4 flex items-center gap-2 text-xs text-destructive"
    >
      <span className="truncate">{message}</span>
      <button
        type="button"
        onClick={onAction}
        className="shrink-0 font-medium underline underline-offset-4 hover:opacity-80"
      >
        {actionLabel}
      </button>
    </div>
  )
}

function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label className="text-sm font-medium text-foreground">{label}</label>
      {children}
      {hint ? (
        <p className="text-xs text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  )
}

export function SettingsView() {
  const queryClient = useQueryClient()

  const {
    data: settings,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["llm-settings"],
    queryFn: fetchLlmSettings,
    refetchInterval: 5 * 60_000,
    refetchIntervalInBackground: false,
  })

  const { data: providers = [] } = useQuery({
    queryKey: ["llm-providers"],
    queryFn: fetchLlmProviders,
  })

  const [form, setForm] = useState<FormState | null>(null)
  const [apiKey, setApiKey] = useState("")
  const [clearKey, setClearKey] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedMessage, setSavedMessage] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null)

  useEffect(() => {
    if (!settings) return
    setForm(toForm(settings))
    setApiKey("")
    setClearKey(false)
  }, [settings])

  const providerItems = useMemo(
    () => providers.map((preset) => ({ label: preset.label, value: preset.id })),
    [providers],
  )

  const saveMutation = useMutation({
    mutationFn: (update: LlmSettingsUpdate) => updateLlmSettings(update),
    onSuccess: (updated) => {
      setSaveError(null)
      setSavedMessage("LLM settings saved.")
      setForm(toForm(updated))
      setApiKey("")
      setClearKey(false)
      queryClient.invalidateQueries({ queryKey: ["llm-settings"] })
      queryClient.invalidateQueries({ queryKey: ["llm-providers"] })
    },
    onError: (err) => {
      setSavedMessage(null)
      setSaveError(err instanceof Error ? err.message : "Failed to save")
    },
  })

  const testMutation = useMutation({
    mutationFn: testLlmConnection,
    onSuccess: (result) => setTestResult(result),
    onError: (err) => {
      setTestResult({
        ok: false,
        error: err instanceof Error ? err.message : "Connection test failed",
      })
    },
  })

  function updateForm(patch: Partial<FormState>) {
    setForm((prev) => (prev ? { ...prev, ...patch } : prev))
  }

  function handleProviderChange(id: string) {
    const preset = providers.find((item) => item.id === id)
    setForm((prev) => {
      if (!prev) return prev
      return {
        ...prev,
        provider: id,
        base_url: preset?.default_base_url
          ? preset.default_base_url
          : prev.base_url,
        model: preset?.default_model ? preset.default_model : prev.model,
      }
    })
  }

  function positive(value: string): boolean {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed > 0
  }

  function buildUpdate(): LlmSettingsUpdate | null {
    if (!form) return null
    if (!form.base_url.trim() || !/^https?:\/\//.test(form.base_url.trim())) {
      setSaveError("Base URL must start with http:// or https://.")
      return null
    }
    if (!form.model.trim()) {
      setSaveError("Model must not be empty.")
      return null
    }
    const timeouts = [
      form.connect_timeout,
      form.read_timeout,
      form.write_timeout,
      form.pool_timeout,
    ]
    if (!timeouts.every(positive)) {
      setSaveError("Timeouts must be positive numbers.")
      return null
    }
    const maxRetries = Number(form.max_retries)
    if (!Number.isInteger(maxRetries) || maxRetries < 0) {
      setSaveError("Max retries must be zero or a positive integer.")
      return null
    }
    setSaveError(null)
    const update: LlmSettingsUpdate = {
      provider: form.provider,
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      summary_model: form.summary_model.trim(),
      enable_thinking: form.enable_thinking,
      thinking_template: form.thinking_template,
      connect_timeout: Number(form.connect_timeout),
      read_timeout: Number(form.read_timeout),
      write_timeout: Number(form.write_timeout),
      pool_timeout: Number(form.pool_timeout),
      max_retries: maxRetries,
    }
    if (clearKey) update.api_key = ""
    else if (apiKey) update.api_key = apiKey
    return update
  }

  function handleSaveClick() {
    setSavedMessage(null)
    if (buildUpdate()) setConfirmOpen(true)
  }

  function handleConfirm() {
    const update = buildUpdate()
    if (update) saveMutation.mutate(update)
  }

  const isSaving = saveMutation.isPending
  const keyPlaceholder = settings?.api_key.configured
    ? `Saved (${settings.api_key.preview}) — leave blank to keep`
    : "Not set"

  return (
    <div className="flex-1 overflow-y-auto px-6 pt-24 pb-10">
      <div className="mx-auto w-full max-w-3xl">
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-2xl font-bold tracking-tight text-foreground">
            Settings
          </h1>
          {settings ? (
            <Badge variant={settings.configured ? "secondary" : "outline"}>
              {settings.configured ? "Configured" : "Not configured"}
            </Badge>
          ) : null}
        </div>

        {error && (
          <ErrorBanner
            message={
              error instanceof Error
                ? error.message
                : "Failed to load LLM settings"
            }
            actionLabel="Retry"
            onAction={() => {
              queryClient.invalidateQueries({ queryKey: ["llm-settings"] })
            }}
          />
        )}

        {saveError && (
          <ErrorBanner
            message={saveError}
            actionLabel="Dismiss"
            onAction={() => setSaveError(null)}
          />
        )}

        {savedMessage && (
          <p className="mt-4 flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
            <Check className="size-3.5" />
            {savedMessage}
          </p>
        )}

        {isLoading || !form ? (
          <div className="mt-6 space-y-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-24 animate-pulse bg-muted/40" />
            ))}
          </div>
        ) : (
          <div className="mt-6 flex flex-col gap-5">
            <Card>
              <CardHeader>
                <CardTitle>Provider</CardTitle>
                <CardDescription>
                  Pick a preset or use Custom for any OpenAI-compatible endpoint.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <Field label="Provider">
                  <Select
                    items={providerItems}
                    value={form.provider}
                    onValueChange={(value) => {
                      if (typeof value === "string" && value) {
                        handleProviderChange(value)
                      }
                    }}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select a provider" />
                    </SelectTrigger>
                    <SelectContent>
                      {providers.map((preset) => (
                        <SelectItem key={preset.id} value={preset.id}>
                          {preset.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Base URL">
                  <Input
                    value={form.base_url}
                    onChange={(event) =>
                      updateForm({ base_url: event.target.value })
                    }
                    placeholder="https://api.openai.com/v1"
                    autoComplete="off"
                  />
                </Field>

                <Field label="Model">
                  <Input
                    value={form.model}
                    onChange={(event) =>
                      updateForm({ model: event.target.value })
                    }
                    placeholder="gpt-4o-mini"
                    autoComplete="off"
                  />
                </Field>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>API key</CardTitle>
                <CardDescription>
                  Stored on the server and never shown again. Leave blank to
                  keep the current key.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <Field label="Key">
                  <div className="flex items-center gap-2">
                    <Input
                      type="password"
                      value={apiKey}
                      onChange={(event) => {
                        setApiKey(event.target.value)
                        if (event.target.value) setClearKey(false)
                      }}
                      placeholder={keyPlaceholder}
                      autoComplete="off"
                      disabled={clearKey}
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="lg"
                      onClick={() => {
                        setApiKey("")
                        setClearKey(
                          !clearKey && settings?.api_key.configured === true,
                        )
                      }}
                      disabled={!settings?.api_key.configured && !clearKey}
                    >
                      <KeyRound />
                      {clearKey ? "Keep existing" : "Clear"}
                    </Button>
                  </div>
                </Field>
                {clearKey ? (
                  <p className="text-xs text-destructive">
                    The stored API key will be removed when you save.
                  </p>
                ) : null}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Options</CardTitle>
                <CardDescription>
                  Optional model used for summaries, plus advanced provider
                  tuning.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <Field
                  label="Summary model"
                  hint="Defaults to the main model when empty."
                >
                  <Input
                    value={form.summary_model}
                    onChange={(event) =>
                      updateForm({ summary_model: event.target.value })
                    }
                    placeholder={form.model}
                    autoComplete="off"
                  />
                </Field>

                <label className="flex items-center justify-between gap-4">
                  <span className="text-sm font-medium text-foreground">
                    Enable model thinking
                  </span>
                  <Switch
                    checked={form.enable_thinking}
                    onCheckedChange={(checked) =>
                      updateForm({ enable_thinking: checked })
                    }
                  />
                </label>

                <label className="flex items-center justify-between gap-4">
                  <span className="text-sm font-medium text-foreground">
                    Send thinking template
                  </span>
                  <Switch
                    checked={form.thinking_template}
                    onCheckedChange={(checked) =>
                      updateForm({ thinking_template: checked })
                    }
                  />
                </label>

                <div className="grid grid-cols-2 gap-4">
                  <Field label="Connect timeout (s)">
                    <Input
                      type="number"
                      min="0"
                      step="0.1"
                      value={form.connect_timeout}
                      onChange={(event) =>
                        updateForm({ connect_timeout: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="Read timeout (s)">
                    <Input
                      type="number"
                      min="0"
                      step="0.1"
                      value={form.read_timeout}
                      onChange={(event) =>
                        updateForm({ read_timeout: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="Write timeout (s)">
                    <Input
                      type="number"
                      min="0"
                      step="0.1"
                      value={form.write_timeout}
                      onChange={(event) =>
                        updateForm({ write_timeout: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="Pool timeout (s)">
                    <Input
                      type="number"
                      min="0"
                      step="0.1"
                      value={form.pool_timeout}
                      onChange={(event) =>
                        updateForm({ pool_timeout: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="Max retries">
                    <Input
                      type="number"
                      min="0"
                      step="1"
                      value={form.max_retries}
                      onChange={(event) =>
                        updateForm({ max_retries: event.target.value })
                      }
                    />
                  </Field>
                </div>
              </CardContent>
            </Card>

            {testResult ? (
              <div
                className={cn(
                  "flex items-center gap-2 text-xs",
                  testResult.ok
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-destructive",
                )}
              >
                {testResult.ok ? (
                  <Check className="size-3.5" />
                ) : (
                  <AlertTriangle className="size-3.5" />
                )}
                {testResult.ok
                  ? `Connection OK${
                      typeof testResult.models_count === "number"
                        ? ` · ${testResult.models_count} models`
                        : ""
                    }`
                  : `Connection failed: ${testResult.error ?? "unknown error"}`}
              </div>
            ) : null}

            <div className="flex items-center justify-end gap-2">
              <Button
                type="button"
                variant="outline"
                size="lg"
                onClick={() => {
                  setTestResult(null)
                  testMutation.mutate()
                }}
                disabled={testMutation.isPending || !settings?.configured}
              >
                {testMutation.isPending ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <PlugZap />
                )}
                Test connection
              </Button>
              <Button
                type="button"
                size="lg"
                onClick={handleSaveClick}
                disabled={isSaving}
              >
                {isSaving ? (
                  <Loader2 className="animate-spin" />
                ) : (
                  <Save />
                )}
                Save changes
              </Button>
            </div>
          </div>
        )}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Apply LLM settings?</AlertDialogTitle>
            <AlertDialogDescription>
              The new provider configuration takes effect on the next message.
              No restart is required.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleConfirm}>Apply</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
