/** Parse a failed response body into a human-readable message. */
export async function readError(response: Response): Promise<string> {
  try {
    const data = await response.json()
    const message = data?.detail ?? data?.error
    if (typeof message === "string" && message) return message
    if (Array.isArray(message)) {
      const messages = message
        .map((item) => (item && typeof item.msg === "string" ? item.msg : null))
        .filter((item): item is string => item !== null)
      if (messages.length > 0) return messages.join("; ")
    }
  } catch {
    // response had no JSON body
  }
  return `${response.status} ${response.statusText}`.trim()
}
