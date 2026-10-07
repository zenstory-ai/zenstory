# M07 R5 SSE outer-pump bounded queue plan

Actual finite paused-consumer32/256 characterization2PASS: produced32/256, consumed1, queued32/256, producerdone. This measures entry growth, not functional RED/p95/bytes. Target32queue entries plus atmostone producer-local frame; preserve order/errors/sentinels/context/heartbeat/deadline.

IndependentDESIGN initiallyBLOCK simpleboundedqueue: cancel whileawaitingput missesgenerator cleanup. Approvedadjustment: awaitframe/error/done puts, deterministicallyaclose actualsource whenproducercancelled (originalCancelledError preserved), _primed_stream finally ownsaclose nestedactualprocess_stream. Onlypump+registeredAPIlocalwrapper, not Agentservice/workflow queues. Addfullcompletion/failure/cancel/deadline andactualAPI disconnect cleanup controls beforefinalsourceapproval; noframework/locks/deps.
