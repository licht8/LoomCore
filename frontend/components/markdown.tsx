import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"

import { cn } from "@/lib/utils"

const components: Components = {
  h1: ({ node, ...props }) => <h1 className="mt-4 mb-2 text-lg font-bold" {...props} />,
  h2: ({ node, ...props }) => <h2 className="mt-4 mb-2 text-base font-bold" {...props} />,
  h3: ({ node, ...props }) => <h3 className="mt-3 mb-1.5 text-sm font-semibold" {...props} />,
  p: ({ node, ...props }) => <p className="my-2 leading-relaxed" {...props} />,
  ul: ({ node, ...props }) => <ul className="my-2 list-disc space-y-1 pl-5" {...props} />,
  ol: ({ node, ...props }) => <ol className="my-2 list-decimal space-y-1 pl-5" {...props} />,
  li: ({ node, ...props }) => <li className="leading-relaxed" {...props} />,
  strong: ({ node, ...props }) => <strong className="font-semibold" {...props} />,
  em: ({ node, ...props }) => <em className="italic" {...props} />,
  a: ({ node, ...props }) => (
    <a
      className="text-primary underline hover:opacity-80"
      target="_blank"
      rel="noopener noreferrer"
      {...props}
    />
  ),
  blockquote: ({ node, ...props }) => (
    <blockquote className="my-2 border-l-2 border-border pl-3 italic text-muted-foreground" {...props} />
  ),
  hr: ({ node, ...props }) => <hr className="my-3 border-border" {...props} />,
  pre: ({ node, ...props }) => (
    <pre
      className="my-2 overflow-x-auto rounded-lg bg-[oklch(0.28_0.06_285)] p-3 font-mono text-xs leading-relaxed text-[oklch(0.92_0.05_310)]"
      {...props}
    />
  ),
  code: ({ node, className, children, ...props }) => {
    const isBlock = /language-/.test(className ?? "") || String(children).includes("\n")
    return isBlock ? (
      <code className={cn("font-mono text-xs", className)} {...props}>
        {children}
      </code>
    ) : (
      <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs" {...props}>
        {children}
      </code>
    )
  },
  table: ({ node, ...props }) => (
    <table className="my-2 w-full border-collapse text-left text-xs" {...props} />
  ),
  th: ({ node, ...props }) => (
    <th className="border border-border px-2 py-1 font-semibold" {...props} />
  ),
  td: ({ node, ...props }) => <td className="border border-border px-2 py-1" {...props} />,
}

/** Render assistant/user message content as Markdown (GFM). Raw HTML is not enabled. */
export function Markdown({ content }: { content: string }) {
  return (
    <div className="break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
