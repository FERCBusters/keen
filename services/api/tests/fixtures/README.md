# KEEN Agent OTLP contract fixture

`keen-agent-otlp.json` is the original KEEN Agent v0.1.0 OTLP/HTTP JSON
contract sample from `cmd/keen-agent/testdata/otlp.json`. Keep the fixture in
KEEN so the receiver's contract test runs without checking out the independent
agent repository. It contains synthetic package data and no credentials.

The test verifies the decoded event UUID, action, outcome, package, agent
version and severity. Changes to the transport contract should include explicit
receiver/agent compatibility tests rather than silently replacing this sample.
