# vouch

Every number in your paper, vouched for by the code that produced it.

Experiments **record** values while they run; the paper **cites** them as
`\vouch{key}` and never types a number; `vouch check` proves every cited value
comes from a run whose code has not changed since.

Status: under construction. See [SPEC.md](SPEC.md) for the design.
