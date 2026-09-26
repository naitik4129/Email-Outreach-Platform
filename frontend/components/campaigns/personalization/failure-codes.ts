// Plain-language explanations for the stable failure codes the backend records
// (validator codes, runner codes and preview codes). Codes are safe to show: they
// never contain lead data or generated text.

const DESCRIPTIONS: Record<string, string> = {
  // Validation
  empty_subject: "The generated email had no subject.",
  subject_too_long: "The generated subject was too long.",
  subject_control_chars: "The generated subject was not a single line of plain text.",
  subject_placeholder: "The generated subject contained a placeholder or markup.",
  empty_body: "The generated email had no body.",
  body_too_short: "The generated email was too short.",
  body_too_long: "The generated email was too long.",
  too_many_paragraphs: "The generated email had too many paragraphs.",
  paragraph_too_long: "A paragraph in the generated email was too long.",
  body_control_chars: "The generated email contained unsupported characters.",
  body_placeholder: "The generated email contained a placeholder or markup.",
  unsupported_number:
    "The email stated a number that is not in your reference email, objective or the lead's data.",
  unsupported_url: "The email included a link that is not in your reference email.",
  unsupported_email: "The email included an email address that is not in your reference email.",
  unsupported_phone: "The email included a phone number that is not in your reference email.",
  unknown_fact_id: "The generator cited information that was not provided.",
  no_facts_used: "The generated email did not use any information about the lead.",
  missing_must_mention: "The email left out something your objective says must be mentioned.",
  never_say_violation: "The email used a phrase your objective says must never be used.",
  cta_missing: "The email lost your call to action.",
  reference_intent_lost: "The email drifted too far from your reference email.",
  unsafe_content: "The email contained pushy or sensitive-data language.",
  angle_invalid: "The generator did not describe how it personalized the email.",
  repeats_previous: "The follow-up repeated the previous email.",
  subject_repeated: "The follow-up reused the previous subject.",
  filler_followup: "The follow-up was filler (for example “just following up”).",
  malformed_output: "The generator returned an answer that could not be used.",
  output_truncated: "The generator's answer was cut off.",
  model_refusal: "The generator declined to write this email.",
  // Runner / provider
  attempts_exhausted: "The generator kept failing and the attempts were used up.",
  provider_unavailable: "The AI service was unavailable for too long.",
  model_timeout: "The AI service took too long to respond.",
  model_unavailable: "The AI service was temporarily unavailable.",
  model_rate_limited: "The AI service is rate limiting requests.",
  model_network_error: "The AI service could not be reached.",
  model_configuration_error: "The AI service is not configured correctly.",
  provider_quota: "The AI provider account has run out of quota.",
  provider_401: "The AI provider rejected the API key.",
  provider_403: "The AI provider denied access.",
  provider_404: "The configured AI model was not found.",
  objective_invalid: "The campaign objective is missing or invalid.",
  work_item_unavailable: "The message could not be prepared.",
  daily_cap_reached: "The daily generation limit was reached; it will retry.",
  // Previews
  previous_step_failed: "An earlier email for this lead failed, so this one was skipped.",
  preview_budget_exhausted: "The daily limit for sample generation was reached.",
  rate_limited: "The AI service is rate limiting requests. Try again shortly.",
  campaign_not_draft: "Samples can only be generated while the campaign is a draft.",
};

export function describeFailureCode(code: string): string {
  return DESCRIPTIONS[code] ?? code.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
