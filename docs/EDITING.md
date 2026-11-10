# Editing the workflow

Three files change behaviour; you never need to touch `p3graph/`.

| To change | Edit |
|---|---|
| A prompt | `prompts/<agent>.md` (replace) or `prompts/<agent>.append.md` (add to it) |
| The workflow (add a loop, gate, tool call, branch) | `workflow.yaml` (where) + `custom_nodes.py` (what) |
| A tool the model's evidence can come from | `p3graph/tools.py` (`@tool`) |

Check what you changed: `python -m p3graph prompt a3_profile` and `python -m p3graph nodes`.
Use a different file without editing the default: `python -m p3graph --workflow my.yaml run`.

## 1. Change a prompt

Agents: `a1_disease`, `a2_mechanism`, `a3_profile`, `a6_redteam`.

```bash
python -m p3graph prompt a3_profile > prompts/a3_profile.md     # start from the current prompt, then edit it
echo "Always state the dosing route in must_haves." > prompts/a3_profile.append.md   # or just add a rule
```

The next run uses it. Output must still match the agent's JSON schema, or the `parse` step fails.

## 2. Add a node (loop, safety gate, tool call)

`workflow.yaml` says where, `custom_nodes.py` says what:

```yaml
insert:
  - name: a3_refine              # your node's name (shows in the trace and diagram)
    after: a3_postcheck          # splice it in after this step; `p3graph nodes` lists them all
    node: custom_nodes:refine_loop
    may_goto: [a3_prompt]        # nodes it can jump to (for the diagram only)
    params: {field: must_haves, min: 2, max_loops: 2}
```

Valid `after` values are every step: `a1_retrieve … a1_commit`, `a2_…`, `a3_…`, `a6_…`, plus `setup`
and `report`. A wrong name lists the valid ones. Several inserts after the same step run in file order.

A node is an async function. It returns a state update, or `Command(goto=...)` to jump anywhere:

```python
async def my_gate(state, params):
    o = out(state, params)                                  # this agent's current output (JSON)
    if something_wrong(o):
        return go_back(state, params, "prompt", "Fix X", "why")   # loop back with a comment
    return {"detail": "ok"}                                 # shown in the step trace
```

Shipped examples (all `enabled: false` in `workflow.yaml`; set `true` to try them):

| Node | What it does |
|---|---|
| `safety_gate` | Checks an output for unsourced or overconfident evidence claims and safety words. `on_fail`: `review` (pauses for a human: approve / rerun / reject; in single_shot it only flags), `loop`, `stop`, `flag` |
| `refine_loop` | Goes back to the prompt step with a comment until a condition holds, with a loop cap |
| `run_tools` | Calls tools before the model runs and adds the results to the evidence pack |

Loops are counted in `state["loops"]`. Always give one a cap. Back-jumps reset the retry budget of the step they return to.

## 3. Tools

`run_tools` calls functions from `p3graph/tools.py`:

```python
@tool("my_tool")
async def my_tool(ctx, state, hid, query, **_):
    return [{"id": "tool:my:1", "source": "tool", "source_date": "2019-05-01", "title": "...", "body": "..."}]
```

Returned docs go into the evidence pack, so the model can cite them. Docs dated on or after the
as-of date are dropped, so a tool can never leak the future. Built in: `corpus_search`, `http_json`.

## Limits

- Tools run before the model (retrieval-style). The model does not choose tool calls itself.
- You can insert after any step, but not inside the retry loops (`fix`) or the review checkpoints.
