# M01 no-await auth transaction worker boundary

Three existing async handlers refresh_token, update_current_user and logout perform only synchronous ORM/transaction/rate operations; there are no awaits in these function ASTs. The common User dependency now runs in the ordinary worker, but actual handler SQL remains on the request event loop; the new refresh User gate may block there.

Lock with actual ASGI HTTP, cold default-expiring owned SQLite Session and actual JWT/rotation/User state. SQL listener records actual api.auth handler frames and thread identity, not elapsed timings. Preserve successful rotate/revoke/profile payload and durable state, unique-username failure rollback and invalid-refresh isolation. Override only storage dependency; no auth/service/SQL mocks. Keep pure GET me/register-policy async and all await-bearing registration/login/password/provider functions untouched in this slice.

If genuine RED proves placement, change only three async def to def so FastAPI owns sequential request transaction worker execution. No attached Session concurrent to_thread calls, new helper, pool/session framework or rate policy. Search direct callers before conversion. Independent SOURCE review, fresh affected auth tests plus changed PG revocation suite, unchanged coverage80, Ruff/scoped baseline mypy and source-byte readback; no remote Actions/publication.
