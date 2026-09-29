import { ApiError } from "@/lib/api-client";

const DEFAULT_MESSAGE = "We couldn't complete that request. Please try again.";

// Server-provided messages are already user-safe (ApiError); anything else is
// replaced with a generic message so internals never reach the UI.
//
// A server-side failure (5xx) also carries the request id so the person can quote
// it and support can find the exact log line.
export function errorMessage(error: unknown, fallback: string = DEFAULT_MESSAGE) {
  if (error instanceof ApiError) {
    if (error.status >= 500 && error.requestId) {
      return `${error.message} (Reference: ${error.requestId})`;
    }
    return error.message;
  }
  return fallback;
}

// True for a failure that is worth offering "Try again" for: the server or the
// network, as opposed to a rule the person has to fix (4xx).
export function isRetryableError(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status >= 500);
}
