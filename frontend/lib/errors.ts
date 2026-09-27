import { ApiError } from "@/lib/api-client";

const DEFAULT_MESSAGE = "We couldn't complete that request. Please try again.";

// Server-provided messages are already user-safe (ApiError); anything else is
// replaced with a generic message so internals never reach the UI.
export function errorMessage(error: unknown, fallback: string = DEFAULT_MESSAGE) {
  if (error instanceof ApiError) return error.message;
  return fallback;
}
