# I/O Delegation Jev compaction

This optional Claude Code function hook is globally installable but project-scoped.
It is inert unless the active workspace contains `.io-delegation/compaction.json`
with `enabled: true` and
`approved_data_scope: "conversation_text_and_tool_inputs"`.

This is a broader external-data boundary than the normal Jev router: conversation
text and tool inputs are sent to TypeSafe, while full tool results are represented
by short metadata notes. Failures or insufficient reduction fall back to Claude
Code's built-in compaction.
