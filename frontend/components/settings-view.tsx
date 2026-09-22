"use client"

import { useEffect, useId, useMemo, useRef, useState } from "react"

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
import { ErrorBanner } from "@/components/error-banner"
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
  testLlmConnection,
  updateLlmSettings,
  type LlmSettings,
  type LlmSettingsUpdate,
  type LlmTestResult,
  type ProviderPreset,
} from "@/lib/llm-settings-api"
import { useLlmSettings } from "@/lib/use-llm-settings"
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

function formatTestResult(result: LlmTestResult): string {
  if (!result.ok) {
    return result.error ?? "Connection failed"
  }
  return typeof result.models_count === "number"
    ? `Connection OK · ${result.models_count} models`
    : "Connection OK"
}

const EMPTY_PROVIDERS: ProviderPreset[] = []

function Field({
  label,
  hint,
  children,
  group = false,
}: {
  label: string
  hint?: string
  children: React.ReactNode
  group?: boolean
}) {
  const id = useId()
  const labelId = `${id}-label`
  const labelEl = (
    <span id={labelId} className="text-sm font-medium text-foreground">
      {label}
    </span>
  )
  const body = (
    <>
      {labelEl}
      {children}
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </>
  )
  if (group) {
    return (
      <div
        role="group"
        aria-labelledby={labelId}
        className="flex flex-col gap-1.5"
      >
        {body}
      </div>
    )
  }
  return (
    <label className="flex flex-col gap-1.5">
      {body}
    </label>
  )
}

function NumberField({
  label,
  min,
  step,
  value,
  onChange,
}: {
  label: string
  min: string
  step: string
  value: string
  onChange: (value: string) => void
}) {
  return (
    <Field label={label}>
      <Input
        type="number"
        min={min}
        step={step}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}

export function SettingsView() {
  const queryClient = useQueryClient()

  const { data: settings, isLoading, error } = useLlmSettings()

  const { data: providersData, error: providersError } = useQuery({
    queryKey: ["llm-providers"],
    queryFn: fetchLlmProviders,
  })
  const providers = providersData ?? EMPTY_PROVIDERS

  const [form, setForm] = useState<FormState | null>(null)
  const [apiKey, setApiKey] = useState("")
  const [clearKey, setClearKey] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [savedMessage, setSavedMessage] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null)
  const [pendingUpdate, setPendingUpdate] = useState<LlmSettingsUpdate | null>(
    null,
  )
  const [dirty, setDirty] = useState(false)
  const syncedSettings = useRef<LlmSettings | null>(null)

  useEffect(() => {
    if (!settings || dirty || settings === syncedSettings.current) return
    syncedSettings.current = settings
    setForm(toForm(settings))
  }, [settings, dirty])

  const providerItems = useMemo(() => {
    const items = providers.map((preset) => ({
      label: preset.label,
      value: preset.id,
    }))
    const current = form?.provider
    if (current && !items.some((item) => item.value === current)) {
      items.unshift({ label: current, value: current })
    }
    return items
  }, [providers, form?.provider])

  const saveMutation = useMutation({
    mutationFn: (update: LlmSettingsUpdate) => updateLlmSettings(update),
    onSuccess: (updated) => {
      setDirty(false)
      setSaveError(null)
      setTestResult(null)
      setSavedMessage("LLM settings saved.")
      setForm(toForm(updated))
      setApiKey("")
      setClearKey(false)
      queryClient.setQueryData(["llm-settings"], updated)
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

  function resetTransientState() {
    setTestResult(null)
    setSavedMessage(null)
    setSaveError(null)
  }

  function matchesSaved(
    nextForm: FormState | null,
    nextApiKey: string,
    nextClearKey: boolean,
  ): boolean {
    if (!nextForm || !settings) return false
    if (nextApiKey || nextClearKey) return false
    const saved = toForm(settings)
    return (Object.keys(saved) as (keyof FormState)[]).every(
      (key) => nextForm[key] === saved[key],
    )
  }

  function updateForm(patch: Partial<FormState>) {
    if (!form) return
    const next = { ...form, ...patch }
    const changed = (Object.keys(patch) as (keyof FormState)[]).some(
      (key) => form[key] !== next[key],
    )
    if (!changed) return
    setDirty(!matchesSaved(next, apiKey, clearKey))
    resetTransientState()
    setForm(next)
  }

  function handleKeyInput(value: string) {
    const nextClearKey = value ? false : clearKey
    setDirty(!matchesSaved(form, value, nextClearKey))
    resetTransientState()
    setApiKey(value)
    if (value) setClearKey(false)
  }

  function handleToggleClear() {
    const nextClearKey = !clearKey && settings?.api_key.configured === true
    setDirty(!matchesSaved(form, "", nextClearKey))
    resetTransientState()
    setApiKey("")
    setClearKey(nextClearKey)
  }

  function handleProviderChange(id: string) {
    if (id === form?.provider) return
    const preset = providers.find((item) => item.id === id)
    const next = form
      ? {
          ...form,
          provider: id,
          base_url: preset?.default_base_url ?? "",
          model: preset?.default_model ?? "",
          summary_model: "",
        }
      : form
    setDirty(!matchesSaved(next, "", false))
    resetTransientState()
    setApiKey("")
    setClearKey(false)
    setForm(next)
  }

  function handleDiscard() {
    setDirty(false)
    setApiKey("")
    setClearKey(false)
    resetTransientState()
    if (settings) setForm(toForm(settings))
  }

  function positive(value: string): boolean {
    const parsed = Number(value)
    return Number.isFinite(parsed) && parsed > 0
  }

  function buildUpdate(): {
    update: LlmSettingsUpdate | null
    error: string | null
  } {
    if (!form) return { update: null, error: null }
    if (!form.base_url.trim() || !/^https?:\/\//.test(form.base_url.trim())) {
      return {
        update: null,
        error: "Base URL must start with http:// or https://.",
      }
    }
    if (!form.model.trim()) {
      return { update: null, error: "Model must not be empty." }
    }
    const timeouts = [
      form.connect_timeout,
      form.read_timeout,
      form.write_timeout,
      form.pool_timeout,
    ]
    if (!timeouts.every(positive)) {
      return { update: null, error: "Timeouts must be positive numbers." }
    }
    const maxRetries = Number(form.max_retries)
    if (
      !form.max_retries.trim() ||
      !Number.isInteger(maxRetries) ||
      maxRetries < 0
    ) {
      return {
        update: null,
        error: "Max retries must be zero or a positive integer.",
      }
    }
    const update: LlmSettingsUpdate = {
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
    if (!settings || form.provider !== settings.provider) {
      update.provider = form.provider
    }
    if (clearKey) update.api_key = ""
    else if (apiKey) update.api_key = apiKey
    return { update, error: null }
  }

  function handleSaveClick() {
    setSavedMessage(null)
    const { update, error: validationError } = buildUpdate()
    setSaveError(validationError)
    if (validationError || !update) return
    setPendingUpdate(update)
    setConfirmOpen(true)
  }

  function handleConfirm() {
    if (!pendingUpdate) return
    setSaveError(null)
    saveMutation.mutate(pendingUpdate)
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

        {providersError && (
          <ErrorBanner
            message={
              providersError instanceof Error
                ? providersError.message
                : "Failed to load LLM providers"
            }
            actionLabel="Retry"
            onAction={() => {
              queryClient.invalidateQueries({ queryKey: ["llm-providers"] })
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
          <p
            role="status"
            className="mt-4 flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400"
          >
            <Check className="size-3.5" />
            {savedMessage}
          </p>
        )}

        {isLoading && (
          <div className="mt-6 space-y-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <div key={index} className="h-24 animate-pulse bg-muted/40" />
            ))}
          </div>
        )}

        {!isLoading && !form && (
          <p className="mt-6 py-8 text-center text-sm text-muted-foreground">
            {error ? "LLM settings could not be loaded." : "Loading settings…"}
          </p>
        )}

        {!isLoading && form && (
          <div className="mt-6 flex flex-col gap-5">
            <Card>
              <CardHeader>
                <CardTitle>Provider</CardTitle>
                <CardDescription>
                  Pick a preset or use Custom for any OpenAI-compatible endpoint.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <Field label="Provider" group>
                  <Select
                    items={providerItems}
                    value={form.provider}
                    onValueChange={(value) => {
                      if (typeof value === "string" && value) {
                        handleProviderChange(value)
                      }
                    }}
                  >
                    <SelectTrigger aria-label="Provider">
                      <SelectValue placeholder="Select a provider" />
                    </SelectTrigger>
                    <SelectContent>
                      {providerItems.map((item) => (
                        <SelectItem key={item.value} value={item.value}>
                          {item.label}
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
                  keep the current key — changing the provider clears it unless
                  you enter a new one.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                <Field label="Key" group>
                  <div className="flex items-center gap-2">
                    <Input
                      type="password"
                      aria-label="API key"
                      value={apiKey}
                      onChange={(event) => handleKeyInput(event.target.value)}
                      placeholder={
                        clearKey ? "Will be removed on save" : keyPlaceholder
                      }
                      autoComplete="off"
                    />
                    <Button
                      type="button"
                      variant="outline"
                      size="lg"
                      onClick={handleToggleClear}
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
                {settings &&
                settings.api_key.configured &&
                form.provider !== settings.provider &&
                !apiKey &&
                !clearKey ? (
                  <p className="text-xs text-amber-600 dark:text-amber-400">
                    Changing the provider clears the stored API key unless you
                    enter a new one.
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

                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between gap-4">
                    <span className="text-sm font-medium text-foreground">
                      Enable model thinking
                    </span>
                    <Switch
                      aria-label="Enable model thinking"
                      checked={form.enable_thinking}
                      disabled={!form.thinking_template}
                      onCheckedChange={(checked) =>
                        updateForm({ enable_thinking: checked })
                      }
                    />
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Only sent when the thinking template is enabled below.
                  </p>
                </div>

                <div className="flex items-center justify-between gap-4">
                  <span className="text-sm font-medium text-foreground">
                    Send thinking template
                  </span>
                  <Switch
                    aria-label="Send thinking template"
                    checked={form.thinking_template}
                    onCheckedChange={(checked) =>
                      updateForm(
                        checked
                          ? { thinking_template: true }
                          : { thinking_template: false, enable_thinking: false },
                      )
                    }
                  />
                </div>

                <div className="grid grid-cols-2 gap-4">
                  <NumberField
                    label="Connect timeout (s)"
                    min="0.1"
                    step="0.1"
                    value={form.connect_timeout}
                    onChange={(value) => updateForm({ connect_timeout: value })}
                  />
                  <NumberField
                    label="Read timeout (s)"
                    min="0.1"
                    step="0.1"
                    value={form.read_timeout}
                    onChange={(value) => updateForm({ read_timeout: value })}
                  />
                  <NumberField
                    label="Write timeout (s)"
                    min="0.1"
                    step="0.1"
                    value={form.write_timeout}
                    onChange={(value) => updateForm({ write_timeout: value })}
                  />
                  <NumberField
                    label="Pool timeout (s)"
                    min="0.1"
                    step="0.1"
                    value={form.pool_timeout}
                    onChange={(value) => updateForm({ pool_timeout: value })}
                  />
                  <NumberField
                    label="Max retries"
                    min="0"
                    step="1"
                    value={form.max_retries}
                    onChange={(value) => updateForm({ max_retries: value })}
                  />
                </div>
              </CardContent>
            </Card>

            {testResult ? (
              <div
                role="status"
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
                {formatTestResult(testResult)}
              </div>
            ) : null}

            <p className="text-right text-xs text-muted-foreground">
              Test connection probes the saved configuration, not unsaved edits.
            </p>

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
              {dirty ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="lg"
                  onClick={handleDiscard}
                  disabled={isSaving}
                >
                  Discard changes
                </Button>
              ) : null}
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

      <AlertDialog
        open={confirmOpen}
        onOpenChange={(open) => {
          setConfirmOpen(open)
          if (!open) setPendingUpdate(null)
        }}
      >
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
