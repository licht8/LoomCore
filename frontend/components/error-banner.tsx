"use client"

export function ErrorBanner({
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
      className="mt-4 flex items-start gap-2 text-xs text-destructive"
    >
      <span className="min-w-0 break-words">{message}</span>
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
