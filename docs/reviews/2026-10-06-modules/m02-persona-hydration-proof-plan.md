# M02 persona form: late hydration must preserve edited fields

## Contract and proof

An editable form must not replace a field the current author has changed while
its initial server profile request is pending. Untouched fields should still
restore the server profile. Cached local profile is an initial value, not proof
that a field was edited during this page visit.

Test the actual OnboardingPersonaPage/AuthProvider/auth identity query boundary,
real QueryClient, storage helpers, persona API and apiClient. Intercept only
fetch/analytics SDK exports; hold an actual GET after the form becomes editable,
change each of the three fields, then deliver the valid current-user profile.
Default and root StrictMode; compare visible selected controls, the final PUT
payload and successful destination. Pristine/cached-profile controls must still
hydrate normally. No auth/query/page/ORM substitute or real provider request.

Before any product edit, capture the phase baseline and valid RED receipts. The
candidate repair is a small per-field dirty ref using existing page state and
event handlers; not a form framework, draft store, server/schema/API change or
blanket ignore of incoming profiles. Seek independent design review after proof.

Fresh affected page/helper/API tests, unchanged Web coverage gate, types/lint and
one proper-CSS App build for the source delta. Positively owned QueryClients,
deferred responses, storage/history, renderers and transient setup cleaned; no
Actions, publication, installs, credentials or production operations.
