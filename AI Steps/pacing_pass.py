"""Pacing pass: rewrites the commentary events that came out too short or too long for their gap.

The commentary AI writes about 80% of the words it's asked for, which leaves dead air between events. This counts
every event against its "Commentate in X words" budget, sends only the ones that are off back to the AI (with the
lines around them, so the conversation still flows), and keeps a rewrite only when it lands closer to the budget.

Run by gemini_task.py after the Commentary step, or on its own:
    python pacing_pass.py 07_events_timed.txt 08_commentary.txt [commentary prompt file]
"""
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

from google import genai
from google.genai import types

SHORT = 0.85       # under this share of the budget, an event gets more words
OVER_BY = 3        # more than this many words over the budget, an event gets cut
ROUNDS = 2
BATCH = 12         # events per AI request: one huge request is slow and its reply can come back cut off
PARALLEL = 4
# Moments that may run long (the commentary prompt allows it): never cut these
MAY_RUN_LONG = ("[ACCIDENT]", "[FINISH]")

LINE = re.compile(r"^(\d{2}:\d{2}:\d{2}) - \[(.+?)\] (.*)$")
EVENT = re.compile(r"^(\d{2}:\d{2}:\d{2}) - (\[[A-Z_]+\])(.*?)\s*Commentate in (\d+) words\s*$")

TASK = """The commentary has been written following the instructions above. You are now its editor, fixing its PACING.
Each event has a word budget: the
spoken words that fit before the next event. Some events came out too short (dead air: the booth goes quiet) or too
long (the next call starts late). Rewrite ONLY the events listed below so each one lands on its target word count.

How to fix a SHORT event: add real talk, never padding. Develop ONE thought properly: have one of them ask the other
something and get a proper answer, let the expert explain what the driver is doing and why, or what it's like in the
car, carry on the conversation from the lines before, or bring in the championship or how this driver's race has
gone. Usually that means making one line longer or adding one line with substance. Keep the lines that are already
there unless changing them makes it flow better.
Padding that gives it away (never do these):
* stub lines added just to make up the count: "Clear air.", "He certainly isn't!", "Not done yet though!",
  "He's right with him.", "Tyres getting hot already." A short line is only fine as a genuine reaction to something
  that just happened on track.
* log notices bolted onto the end of a line: "...ATI Boy's back on track too.", "Nortje heads into the lane, but".
  Leave minor pit entries and exits out, or make them the subject of the talk.
* restating what's already been said, or describing the battle again.
* filler words: massive, brilliant, incredible, unbelievable, glued, relentless, masterclass. While you're
  rewriting an event, take out any that are already in it.
* corner or straight names the event's log line doesn't give.
How to fix a LONG event: cut to the essential call and the best reaction.

Rules:
* Keep every voice, rule and banned phrase from the commentary instructions above. It must still sound transcribed
  from a live broadcast, not written.
* Don't repeat anything said in the lines just before or just after, and don't repeat the battle you already described.
* Nothing may happen before the log says it does: no pass, contact or pit stop that this event's log line doesn't
  confirm.
* Fill with talk, not with new facts: no new radio messages, rumours, damage, failures or story twists that aren't
  in the log or the lines already written. Keep every radio quote and stewards' message that's already there, word
  for word.
* The lines after the event are shown so you can lead into them; don't steal what they say.
* Speakers must be one of: {speakers}.
* Count the words of each line you write and put the number in "words". Hit the target: within {tolerance} words
  under it, never over.

Reply with JSON only:
{{"events": [{{"time": "HH:MM:SS", "lines": [{{"speaker": "...", "text": "...", "words": 0}}]}}]}}

EVENTS TO FIX:
{events}"""


def words(text):
    return len(text.split())


def read_budgets(timed_path):
    """time -> (event tag, event text, budget) from the timed events file."""
    events = {}
    with open(timed_path, encoding="utf-8") as f:
        for line in f:
            m = EVENT.match(line.strip())
            if m:
                events[m.group(1)] = (m.group(2), (m.group(2) + m.group(3)).strip(), int(m.group(4)))
    return events


def read_commentary(path):
    """The file's lines, plus the commentary lines grouped by time in order: [(time, [(speaker, text)])]."""
    with open(path, encoding="utf-8") as f:
        raw = f.read().splitlines()
    groups = []
    for line in raw:
        m = LINE.match(line.strip())
        if not m:
            continue
        if not groups or groups[-1][0] != m.group(1):
            groups.append((m.group(1), []))
        groups[-1][1].append((m.group(2), m.group(3)))
    return raw, groups


def needs_fixing(tag, budget, spoken):
    if budget - spoken >= 4 and spoken < budget * SHORT:
        return True
    return spoken > budget + OVER_BY and tag not in MAY_RUN_LONG


def describe(groups, i, events):
    """One event to fix, with the lines around it."""
    time, lines = groups[i]
    tag, text, budget = events[time]
    spoken = sum(words(t) for _, t in lines)
    before = [f"  {t} - [{s}] {x}" for t, ls in groups[max(0, i - 2):i] for s, x in ls]
    after = [f"  {groups[i + 1][0]} - [{s}] {x}" for s, x in groups[i + 1][1]] if i + 1 < len(groups) else []
    change = f"add about {budget - spoken}" if spoken < budget else f"cut about {spoken - budget}"
    return "\n".join([
        f"--- {time} | target {budget} words | now {spoken} ({change}) ---",
        f"Log: {text}",
        "Lines before:", *(before or ["  (none)"]),
        "Current lines:", *[f"  [{s}] {x}" for s, x in lines],
        "Lines after:", *(after or ["  (none)"]),
    ])


def ask(client, model, config, prompt_text, request):
    response = client.models.generate_content(model=model, contents=f"{prompt_text}\n\n{request}", config=config)
    text = (response.text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    return json.loads(text).get("events", [])


def pace(timed_path, commentary_path, prompt_text="", client=None, model=None):
    events = read_budgets(timed_path)
    raw, groups = read_commentary(commentary_path)
    speakers = sorted({s for _, ls in groups for s, _ in ls})
    if not events or not groups:
        print("Pacing pass: nothing to check.")
        return

    model = model or os.environ.get("GEMINI_MODEL_NAME", "gemini-3.8-flash")
    client = client or genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    level = os.environ.get("GEMINI_THINKING_LEVEL", "MEDIUM").upper()
    config = types.GenerateContentConfig(
        max_output_tokens=int(os.environ.get("GEMINI_MAX_OUTPUT_TOKENS") or 65536),
        response_mime_type="application/json",
        thinking_config=types.ThinkingConfig(thinking_level=level) if level != "NONE" else None)

    replaced = {}
    for round_no in range(1, ROUNDS + 1):
        todo = [i for i, (t, ls) in enumerate(groups)
                if t in events and needs_fixing(events[t][0], events[t][2], sum(words(x) for _, x in ls))]
        print(f"Pacing pass, round {round_no}: {len(todo)} of {len(groups)} events are too short or too long.")
        if not todo:
            break

        def fix(batch):
            request = TASK.format(speakers=", ".join(speakers), tolerance=3,
                                  events="\n\n".join(describe(groups, i, events) for i in batch))
            try:
                return ask(client, model, config, prompt_text, request)
            except Exception as e:
                print(f"  A batch of {len(batch)} events couldn't be fixed ({e}); they stay as they were.")
                return []

        batches = [todo[k:k + BATCH] for k in range(0, len(todo), BATCH)]
        with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
            answers = [a for found in pool.map(fix, batches) for a in found]
        index = {t: i for i, (t, _) in enumerate(groups)}
        better = 0
        for answer in answers:
            i = index.get(answer.get("time"))
            new = [(l.get("speaker"), (l.get("text") or "").strip()) for l in answer.get("lines") or []]
            new = [(s, x) for s, x in new if s in speakers and x]
            if i is None or i not in todo or not new:
                continue
            budget = events[groups[i][0]][2]
            old_off = abs(budget - sum(words(x) for _, x in groups[i][1]))
            new_off = abs(budget - sum(words(x) for _, x in new))
            if new_off < old_off:
                groups[i] = (groups[i][0], new)
                replaced[groups[i][0]] = new
                better += 1
        print(f"  {better} events rewritten closer to their budget.")

    if not replaced:
        return
    # Rebuild the file: each rewritten event's lines go where its first line was
    out, done = [], set()
    for line in raw:
        m = LINE.match(line.strip())
        if m and m.group(1) in replaced:
            if m.group(1) not in done:
                out.extend(f"{m.group(1)} - [{s}] {x}" for s, x in replaced[m.group(1)])
                done.add(m.group(1))
            continue
        out.append(line)
    with open(commentary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    used = [sum(words(x) for _, x in ls) / events[t][2] for t, ls in groups if t in events and events[t][2]]
    print(f"Pacing pass done: {len(replaced)} events rewritten, median budget use now {sorted(used)[len(used) // 2]:.0%}.")


if __name__ == "__main__":
    prompt = ""
    if len(sys.argv) > 3:
        with open(sys.argv[3], encoding="utf-8") as f:
            prompt = f.read()
    pace(sys.argv[1], sys.argv[2], prompt)
