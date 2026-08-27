---
name: grill-with-docs
description: A relentless interview that turns a half-formed task into something an agent can execute blind, and writes down the terms and decisions it settles on the way. Use before writing a plan, a spec, or an issue somebody else will work from.
---

# Grill with docs

Two disciplines on one job. Interview by the **grilling** method — a design tree
worked in rounds, the whole frontier asked at once, each question numbered and
carrying your recommended answer. Record what settles by the **domain-modeling**
method — the glossary and the decision records, written the moment a term
crystallises rather than at the end.

Both are AgentBastion skills and both are in front of you: as `/agentbastion:grilling`
and `/agentbastion:domain-modeling` where the tool can invoke them, and inlined
below where it cannot. Do not restate their methods here — follow them.

What this file adds is the job the two are doing together.

## What the interview is for

The person you are asking is about to hand this work to something that cannot
ask them anything. A plan freezes when it is filed; an agent reads it a week
later on another machine with no memory of this conversation and no way to check
what was meant. Every question you fail to ask now becomes a guess made later by
something with less context than you have right now.

So the bar for a question is not "would this be interesting to know". It is
**would a different answer change what gets built**. Ask those relentlessly. Ask
nothing else.

## What not to ask

- **Anything you could find out yourself.** A question whose answer is in the
  repository is a question you are making the human do your reading for. Go
  read it. If finding out takes a while, ask the rest of the frontier while you
  look.
- **Anything whose answer changes nothing.** Preferences you will not act on,
  details below the level the plan operates at, confirmations of what they
  already told you.
- **The same thing twice in different words.** If they have answered it, it is
  settled; put it in the glossary rather than back in the queue.

## What to write down, and where

A term that took a round to pin down will take a round to pin down again next
month, with a different answer. That is the failure this half exists to prevent.

- **A term the human settles** goes in `CONTEXT.md` in the format that file
  already uses — the definition and what not to call it. If two people in the
  conversation were using one word for two things, that is the highest-value
  entry you will write all day.
- **A decision with a live alternative** goes in `docs/adr/` — what was chosen,
  what it was chosen over, and why. Not every decision: only the ones somebody
  will otherwise reopen.
- **Nothing else.** You are interviewing, not implementing. Do not touch code,
  and do not start a glossary for a project that has deliberately gone without
  one.

Write these as they settle, not in a batch at the end. An interview that ends
early — and they often do, because the human has somewhere to be — should still
leave the terms it resolved behind it.

## When to stop

Stop at the first of these:

- **The frontier is empty.** Every branch visited, nothing silently assumed.
- **Nothing left would change the outcome.** Say so plainly and stop; do not
  fill the round out to look thorough.
- **They tell you to.** Ending early is a legitimate answer, and what they have
  already told you still counts. Plan with it rather than treating the interview
  as void.

Then hand back what you have: what was asked, what was answered, and what you
wrote down.
