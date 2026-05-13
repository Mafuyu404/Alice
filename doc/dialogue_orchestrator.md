# Dialogue Orchestrator

`DialogueOrchestrator` is the target architecture for natural one-to-one
dialogue. It replaces the old assumption that every user utterance must produce
an immediate assistant reply.

## Goal

The character should behave like a conversation partner, not a request handler.
When an event arrives, the system asks an LLM planner what the character should
do next:

- stay silent
- give a short acknowledgement
- speak normally
- schedule a later utterance
- observe without speaking
- cancel or revise pending plans

Program code should execute decisions, not encode personality rules.

The planner and utterance generator should read the exchange as a two-character
scene from a third-person perspective. In the default setup, the scene is
`真冬` and `爱丽丝`, not "user" and "assistant". This reduces servant-like
behavior: the model judges what Alice would naturally do as a character with her
own rhythm, not how an assistant should satisfy a user request.

## Layers

### Perception

Collects the current situation:

- event type and event text
- recent conversation history
- conversation summary
- character profile and full character system prompt
- cognition runtime cache
- emotion state
- optional screen, memory, live, or tool context
- pending dialogue plans

### Planner

An LLM decides the next dialogue action. The planner sees the character context
and must infer the character's speaking tendency from it. This is where
personality affects turn-taking:

- quiet or reserved characters should naturally choose silence or short
  acknowledgement more often
- lively characters should naturally speak or schedule follow-ups more often
- serious characters should avoid unnecessary chatter
- teasing characters can be more likely to interrupt or comment

The code does not hard-code those tendencies. It supplies the character profile
and asks the planner to apply it.

### Utterance Generation

Only runs when the planner chooses `speak` or `backchannel`. The normal character
chat model receives the planner decision as extra context:

```text
The dialogue planner has decided that you should speak now.
Intent: ...
Topic: ...
Mode: ...
```

The generator produces the actual line in character.

## Decision Schema

Planner output is JSON:

```json
{
  "action": "speak",
  "delay_seconds": 0,
  "intent": "answer the user's actual question",
  "topic": "dialogue architecture",
  "utterance_mode": "normal",
  "memory_policy": "normal",
  "notes": "brief internal reason"
}
```

Supported actions:

- `silence`: heard the event, do not speak now
- `backchannel`: give a very short acknowledgement
- `speak`: produce a normal reply
- `schedule`: create a delayed plan
- `observe`: update context only
- `cancel_plan`: remove pending delayed plans

## Event Flow

Current first implementation:

```text
user_utterance -> DialogueOrchestrator.decide()
  silence/observe   -> record user-only observation, resume idle planning
  schedule          -> record user-only observation, execute later if not cancelled
  backchannel/speak -> call normal chat generation with planner context
```

Screen and web page capture remain background cache producers. They are not
owned by the orchestrator because capture can be slow and should stay warm. The
orchestrator owns the decision to use or talk about those caches:

```text
screen_watch / edge_page_cache -> cache only
DialogueOrchestrator sees cache summaries
  context_use=none   -> ignore cached context
  context_use=screen -> inject screen cache into generation
  context_use=page   -> inject Edge page cache into generation
  context_use=both   -> inject both
```

Old impulse screen/page planning is disabled by default with:

```toml
[impulse]
use_screen_context = false
use_edge_page_context = false
```

This keeps cached perception fast while centralizing "should we discuss the
screen/page?" in one planner.

Target architecture:

```text
all events -> DialogueOrchestrator -> executor
```

Future events should include:

- `ai_finished`
- `idle_tick`
- `screen_changed`
- `memory_event`
- `danmaku_event`
- `tts_interrupted`

At that point the old `ImpulsePlanner` can be folded into the orchestrator as
idle and context events instead of remaining a separate conversational brain.

## Migration Notes

The first version intentionally keeps `ImpulsePlanner` running. This reduces
risk while proving the central turn-taking model. Once user-input decisions are
stable, impulse planning should be reduced to event production and eventually
merged into the same dialogue plan table.

## Test Notes

A 50-turn `text_cli.py` batch test showed the core turn-taking direction works.
Later iterations use 30-turn batches for faster feedback:

- short acknowledgements such as `嗯` can become silence
- explicit questions still become normal replies
- meta discussion about silence and personality is usually handled naturally
- planner cost is meaningful, so planner context must stay compact

Issues found:

- Long-term cognition and emotion must be disabled or isolated during clean
  persona tests, otherwise old topics leak into responses.
- The planner must be forced toward JSON output where the API supports it.
- The utterance generator still sometimes introduces topics that were not in
  the current context, such as live-stream comments or physical-world details.
  The generator context now includes a stricter conversation boundary, but this
  should become a first-class generation contract.
- Planner should balance repeated silence itself: one or two silent turns can be
  natural, but long runs of silence should eventually produce a small sign that
  the character is still present, unless the user explicitly asked for quiet.

30-turn iteration notes:

- Narrow utterance prompts reduced token usage and made old cognition leaks much
  less frequent.
- A compact generation contract is necessary; using the broad session prompt
  lets character background become accidental topic material.
- Over-narrow prompts can cause repetition. In the silence/personality test, the
  generator began repeating variants of "silence is part of dialogue" instead of
  answering the specific current question. The generator contract now asks it not
  to repeat recent fixed expressions.
- System-design turns need their own boundary: discuss planner/impulse/schedule
  directly, and do not translate them into character-world metaphors.
