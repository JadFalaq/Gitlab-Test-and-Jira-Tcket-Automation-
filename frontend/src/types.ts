/**
 * Types miroir des schémas Pydantic du backend (app/schemas/*.py).
 * Toute évolution de schéma côté backend doit être répercutée ici.
 */

export type RequirementType = "feature" | "bug" | "improvement" | "other";
export type Priority = "low" | "medium" | "high" | "critical";

export interface RequirementAnalysis {
  title: string;
  type: RequirementType;
  description: string;
  user_personas: string[];
  functional_criteria: string[];
  non_functional_criteria: string[];
  priority: Priority;
  dependencies: string[];
  ambiguities: string[];
  confidence_score: number;
}

export type GherkinKeyword = "Given" | "When" | "Then" | "And" | "But";

export interface GherkinStep {
  keyword: GherkinKeyword;
  text: string;
}

export interface GherkinScenario {
  name: string;
  steps: GherkinStep[];
  tags: string[];
}

export interface GherkinFeature {
  feature_name: string;
  description: string;
  scenarios: GherkinScenario[];
  tags: string[];
}

export interface UserStory {
  title: string;
  as_a: string;
  i_want: string;
  so_that: string;
  acceptance_criteria: string[];
  priority: Priority;
}

export interface TestSummary {
  total_scenarios: number;
  coverage_notes: string;
  suggested_test_types: string[];
}

export interface TestDesignOutput {
  user_stories: UserStory[];
  gherkin_features: GherkinFeature[];
  test_summary: TestSummary;
}

// --- Module 9 : chat de clarification avant l'analyse structurée ---

export type ChatRole = "user" | "assistant";

export interface ChatMessage {
  role: ChatRole;
  content: string;
  created_at: string;
}

export type SessionStatus =
  | "chatting" // module 9 — clarification conversationnelle libre, avant tout appel LLM structuré
  | "analyzing" // module 2 en cours (tâche de fond backend) — à sortir par polling
  | "pending_requirement_review"
  | "requirement_failed"
  | "requirement_rejected"
  | "designing_tests" // module 3 en cours (tâche de fond backend) — à sortir par polling
  | "pending_test_review"
  | "test_design_failed"
  | "tests_rejected"
  | "validated"
  | "ticket_creation_failed"
  | "tickets_created";

// --- Module 5 : Jira/GitLab ---

export type TicketProviderName = "jira" | "gitlab" | "mock";

export interface CreatedTicket {
  provider: TicketProviderName;
  external_id: string;
  key: string;
  url: string;
  title: string;
  status: string;
  session_id: string;
  user_story_title: string;
  created_at: string;
  last_synced_at: string;
}

// --- Module 6 : suivi temps réel ---

export interface TrackedTicket {
  provider: TicketProviderName;
  external_id: string;
  key: string;
  url: string;
  title: string;
  status: string;
  session_id: string;
  session_title: string;
  last_synced_at: string;
}

// --- Module 2 (extension) : pièces jointes (photos/documents) ---

export type AttachmentKind = "image" | "document";

export interface AttachmentResponse {
  id: string;
  filename: string;
  content_type: string;
  kind: AttachmentKind;
  extracted_text: string | null;
  extraction_error: string | null;
  created_at: string;
}

export interface SessionResponse {
  id: string;
  status: SessionStatus;
  raw_input: string;
  context: string | null;
  chat_history: ChatMessage[];
  requirement_analysis: RequirementAnalysis | null;
  test_design_output: TestDesignOutput | null;
  error_message: string | null;
  tickets: CreatedTicket[];
  requirement_regenerations: number;
  test_regenerations: number;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  attachments: AttachmentResponse[];
}

// --- Module 8 : authentification SSO ---

export interface UserResponse {
  id: string;
  email: string;
  display_name: string;
  last_login_at: string;
}

export interface SessionSummary {
  id: string;
  status: SessionStatus;
  title: string;
  created_at: string;
  updated_at: string;
}

// --- Module 7 : analytics ---

export interface StatusBreakdown {
  status: string;
  count: number;
}

export interface TypeBreakdown {
  type: string;
  count: number;
}

export interface PriorityBreakdown {
  priority: string;
  count: number;
}

export interface TicketProviderBreakdown {
  provider: string;
  count: number;
}

export interface AnalyticsSummary {
  total_sessions: number;
  sessions_by_status: StatusBreakdown[];
  sessions_by_type: TypeBreakdown[];
  sessions_by_priority: PriorityBreakdown[];
  average_confidence_score: number | null;
  average_scenarios_per_session: number | null;
  total_requirement_regenerations: number;
  total_test_regenerations: number;
  average_regenerations_per_session: number | null;
  completed_sessions: number;
  rejected_sessions: number;
  acceptance_rate: number | null;
  total_tickets_created: number;
  tickets_by_provider: TicketProviderBreakdown[];
  average_pipeline_duration_seconds: number | null;
}
