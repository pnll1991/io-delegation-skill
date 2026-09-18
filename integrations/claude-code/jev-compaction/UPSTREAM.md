# Upstream provenance

The compaction core is vendored from `tamaratran/fast-jev-compaction` at commit
`e3f262a7f4d42bd8dd32ced30d26176f7cb545b0` under its MIT license.

I/O Delegation changes the host adapter to require a project-local policy and an
explicit approval marker for the broader compaction data scope. The TypeSafe API
key remains in an environment variable and is never written to project config.
