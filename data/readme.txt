UserShaker project export — schema 1.0

One sheet per object type, one row per object. Every row has a stable id (the same from one export to
the next while the object exists), and every relationship is an explicit foreign-key column named after
the id it points to (steps.use_case_id → use_cases.use_case_id). Lists are link sheets, never
separated by commas in a cell. An empty cell means "not set". Text is UTF-8, as typed in the app.

Ids are text (UserShaker ids, about 25 characters), not UUIDs. Never use a name as an id: names change
and are not unique. position and sequence columns run 1, 2, 3… with no gap within their parent.

If some sheets or columns were left out when exporting, foreign keys may point to a sheet that is not
in this file. The meta sheet lists the row count of each sheet that is.

SHEETS

meta — Key / value pairs that describe the file itself. One row per key.
  • key (text; always filled) — schema_version, exported_at, company_name, project_id, project_name, count_<sheet>
  • value (text; always filled) — The value

use_cases — One row per use case of the export, in the project's order.
  • use_case_id (id; always filled) — Id of the use case
  • position (integer; always filled) — Order in the project, 1 = first
  • name (text; always filled) — Use case name
  • description (text) — One-line summary
  • story (text) — The narrative the blueprint was built from
  • status (enum; always filled; values: new, in_progress, ready, frozen) — Workflow status in the app
  • folder (text) — Folder the use case is filed in

step_groups — A group covers consecutive steps of one use case, for example a phase.
  • group_id (id; always filled) — Id of the group
  • use_case_id (id; always filled; → use_cases) — Use case it belongs to
  • position (integer; always filled) — Order in the use case
  • name (text; always filled) — Group name
  • color (text) — Hex colour shown in the app

steps — One row per step: the columns of the blueprint, read left to right.
  • step_id (id; always filled) — Id of the step
  • use_case_id (id; always filled; → use_cases) — Use case it belongs to
  • position (integer; always filled) — Order in the use case, 1 = first
  • name (text; always filled) — Step name
  • description (text) — What happens during the step
  • trigger_text (text) — Free-text trigger, as typed in the app
  • group_id (id; → step_groups) — Group the step belongs to
  • note (text) — Free note
  • image_url (url) — Illustration of the step (opens for signed-in UserShaker users only)

step_triggers — Structured version of what starts a step: "When component is state". The step's first row is the event (trigger), the next ones must also be true (condition).
  • trigger_id (id; always filled) — Id of the row
  • step_id (id; always filled; → steps) — Step it starts
  • kind (enum; always filled; values: trigger, condition) — trigger for the step's first row, condition for the next ones
  • position (integer; always filled) — Order among the step's rows
  • component_id (id; always filled; → components) — Component observed
  • state_id (id; always filled; → component_states) — State it must be in or enter
  • operator (enum; always filled; values: is) — is (only value in 1.0)

personas — The people the use cases are designed for. A persona used in several use cases has one row.
  • persona_id (id; always filled) — Id of the persona
  • name (text; always filled) — Persona name
  • age_category (enum; values: child, young_adult, active_adult, senior) — Age group
  • gender (enum; values: female, male, other) — Gender
  • biography (text) — Background and habits
  • color (text) — Hex colour shown in the app
  • library_id (id) — Team library the persona comes from, if any
  • library_name (text) — Name of that team library
  • library_link_mode (enum; values: linked, copied) — linked (follows the library) or copied (an independent copy)

use_case_personas — Which personas take part in a use case.
  • use_case_id (id; always filled; → use_cases) — Use case
  • persona_id (id; always filled; → personas) — Persona taking part
  • position (integer; always filled) — Row order in the blueprint

persona_step_feedback — What a persona lives at a step. One row per step × persona of the use case.
  • feedback_id (id; always filled) — Id of the row
  • step_id (id; always filled; → steps) — Step
  • persona_id (id; always filled; → personas) — Persona
  • is_present (boolean; always filled) — false when the persona is not part of this step (its texts are kept)
  • action (text) — What the persona does
  • insight (text) — What we learn about them (the app's feedback field)
  • emotion_score (integer) — From -5 (very negative) to +5 (very positive)

components — Every element of the experience. A component used in several use cases has one row.
  • component_id (id; always filled) — Id of the component
  • name (text; always filled) — Component name
  • role (enum; always filled; values: trigger, reactive, object) — trigger (its state starts steps), reactive (its state responds) or object (groups other components)
  • category (text) — Category defined by the team (Light, Sound, Screen…)
  • parent_component_id (id; → components) — Parent object when the component is part of one
  • description (text) — What the component is
  • library_id (id) — Team library the component comes from, if any
  • library_name (text) — Name of that team library
  • library_link_mode (enum; values: linked, copied) — linked (follows the library) or copied (an independent copy)

component_states — The states a component can take, in display order. One list per component, shared by its use cases.
  • state_id (id; always filled) — Id of the state
  • component_id (id; always filled; → components) — Component it belongs to
  • position (integer; always filled) — Display order
  • name (text; always filled) — State name
  • description (text) — What the component does in this state

use_case_components — Which components appear in a use case.
  • use_case_id (id; always filled; → use_cases) — Use case
  • component_id (id; always filled; → components) — Component
  • position (integer; always filled) — Row order in the blueprint

step_component_states — The blueprint grid: the state of each component at each step. A component that changes state during a step has several rows, ordered by sequence.
  • step_id (id; always filled; → steps) — Step
  • component_id (id; always filled; → components) — Component
  • state_id (id; always filled; → component_states) — State at that step
  • sequence (integer; always filled) — 1 for the first state in the step, 2 for the next…

step_unused_components — Components deliberately marked "Not used" at a step: they take no part in that moment, so they have no row in step_component_states there.
  • step_id (id; always filled; → steps) — Step
  • component_id (id; always filled; → components) — Component not used at that step

user_needs — The needs the steps serve, from the team's user-need libraries.
  • user_need_id (id; always filled) — Id of the user need
  • name (text; always filled) — The need, in the user's words
  • description (text) — Detail
  • reference_url (url) — External reference
  • library_id (id; always filled) — User-need library it belongs to
  • library_name (text) — Name of that library

step_user_needs — Which needs each step serves.
  • step_id (id; always filled; → steps) — Step
  • user_need_id (id; always filled; → user_needs) — Need the step serves
