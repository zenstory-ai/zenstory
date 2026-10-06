# M07 R3 suggestions genre — source phase

Actual /api/v1/agent/suggest ignored the Project.project_type returned by its real permission check. The existing service supports novel/short/screenplay. Only retain that object and forward its field to generate_suggestions; no new SQL, abstraction, auth/quota/locale/prompt/fallback changes.

Canonical pre-source five real HTTP/JWT/context/ORM cases: two genuine short/screenplay prompt RED, three novel/foreign/unavailable-provider controls PASS. Faked only local model completion; Project, context assembly, permission and ordinary registered route execute with cold default-expiring Sessions. Output/status/body/persistence and closed Sessions checked before prompt oracle.

Fresh affected four suites91PASS; combined api.agent/agent.suggest_service89.40% unchanged80 gate. Ruff affected products/newtest0. Mypy --shadow-file exactpre-api.agent and current each Success noissues1sourcefile; no global backend claim. Baseline/current logs retained, no unrelated revalidation or remote Actions.

All route/context/provider singleton/dependency/listeners preserved, each owned SQLite engine disposed/file absent, conftest NamedTemporaryFile and primary runtime DB removed, no unknown guesses; cleanup receipts supplied. No lifespan/provider/browser/realpayments. Final source/hash manifest supplied; independent SOURCE review pending. This is a slice, not M07/all23/release closure.
