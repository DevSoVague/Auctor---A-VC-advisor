# Step trace

Per-step inputs and outputs are in `steps/NNN_<node>.json` next to this file.

| # | node | card | ms | what happened |
|---|---|---|---|---|
| 1 | setup |  | 0 | as_of 2021-01-01 · single_shot · llm live |
| 2 | a1_retrieve |  | 0 | 19 docs kept from 8 retrieval calls |
| 3 | a1_prompt |  | 0 | 2 messages, 6,977 chars · with reviewer feedback |
| 4 | a1_llm |  | 35473 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 1955 in / 4335 out · 35473 ms |
| 5 | a1_parse |  | 0 | valid WhyNow |
| 6 | a1_postcheck |  | 0 | 5 shifts kept · no changes needed |
| 7 | a1_validate |  | 0 | ok |
| 8 | a1_commit |  | 0 | 5 shifts (2 negative), 3 segments |
| 9 | a2_retrieve |  | 0 | 27 docs, 13 Open Targets targets, 9 drugs, 18 retrieval calls |
| 10 | a2_prompt |  | 0 | 2 messages, 14,344 chars · with reviewer feedback |
| 11 | a2_llm |  | 70338 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 4440 in / 7136 out · 70338 ms |
| 12 | a2_parse |  | 0 | valid A2Output |
| 13 | a2_grade |  | 0 | 6 proposed → 5 kept, 0 rejected · h3:t1/gold, h4:t1/gold, h5:t1/gold, h1:t2/silver, h2:t2/silver |
| 14 | a2_postcheck |  | 0 | 5 cards · no changes needed |
| 15 | a2_commit |  | 0 | 5 ranked cards → queue ['h3', 'h4', 'h5', 'h1', 'h2'] |
| 16 | a3_retrieve | h3 | 0 | h3 · 13 docs from 4 retrieval calls |
| 17 | a3_retrieve | h4 | 0 | h4 · 13 docs from 4 retrieval calls |
| 18 | a3_retrieve | h5 | 0 | h5 · 13 docs from 4 retrieval calls |
| 19 | a3_retrieve | h1 | 0 | h1 · 13 docs from 4 retrieval calls |
| 20 | a3_retrieve | h2 | 0 | h2 · 13 docs from 4 retrieval calls |
| 21 | a3_prompt | h3 | 0 | h3 · 2 messages, 6,536 chars · with reviewer/red-team feedback |
| 22 | a3_prompt | h4 | 0 | h4 · 2 messages, 6,166 chars · with reviewer/red-team feedback |
| 23 | a3_prompt | h5 | 0 | h5 · 2 messages, 6,189 chars · with reviewer/red-team feedback |
| 24 | a3_prompt | h1 | 0 | h1 · 2 messages, 6,479 chars · with reviewer/red-team feedback |
| 25 | a3_prompt | h2 | 0 | h2 · 2 messages, 6,121 chars · with reviewer/red-team feedback |
| 26 | a3_llm | h3 | 48586 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 1973 in / 5287 out · 48586 ms |
| 27 | a3_llm | h4 | 29474 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 1839 in / 4254 out · 29474 ms |
| 28 | a3_llm | h5 | 31964 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 1860 in / 4480 out · 31964 ms |
| 29 | a3_llm | h1 | 29161 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 1931 in / 6020 out · 29161 ms |
| 30 | a3_llm | h2 | 23023 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 1822 in / 4239 out · 23023 ms |
| 31 | a3_parse | h3 | 0 | valid ProductProfile |
| 32 | a3_parse | h4 | 0 | valid ProductProfile |
| 33 | a3_parse | h5 | 0 | valid ProductProfile |
| 34 | a3_parse | h1 | 0 | valid ProductProfile |
| 35 | a3_parse | h2 | 0 | valid ProductProfile |
| 36 | a3_validate | h3 | 0 | ok |
| 37 | a3_validate | h4 | 0 | ok |
| 38 | a3_validate | h5 | 0 | 1 problem(s), will retry: must-have 'no increase in major adverse cardiac events (MACE) vs placebo' is not measurable: name an endpoint, |
| 39 | a3_fix | h5 | 0 | 1 problem(s) appended to the prompt |
| 40 | a3_llm | h5 | 66298 | call 2 · deepseek-ai/DeepSeek-V4-Flash-0731 · length · 1902 in / 16000 out · 66298 ms · TRUNCATED (hit max_tokens) |
| 41 | a3_parse | h5 | 0 | schema error, will retry: 1 validation error for ProductProfile Invalid JSON: EOF while parsing a value at line 1 column 0 [ty |
| 42 | a3_fix | h5 | 0 | schema error appended to the prompt |
| 43 | a3_llm | h5 | 26046 | call 3 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 1981 in / 5299 out · 26046 ms |
| 44 | a3_parse | h5 | 0 | valid ProductProfile |
| 45 | a3_validate | h5 | 0 | ok |
| 46 | a3_validate | h1 | 0 | ok |
| 47 | a3_validate | h2 | 0 | ok |
| 48 | a3_postcheck | h3 | 0 | 3 stripped_sources, 3 downgraded_to_reasoning |
| 49 | a3_postcheck | h4 | 0 | no changes needed |
| 50 | a3_postcheck | h5 | 0 | no changes needed |
| 51 | a3_postcheck | h1 | 0 | 2 stripped_sources, 2 downgraded_to_reasoning |
| 52 | a3_postcheck | h2 | 0 | no changes needed |
| 53 | a3_commit | h3 | 0 | h3 · 2 must-haves |
| 54 | a3_commit | h4 | 0 | h4 · 2 must-haves |
| 55 | a3_commit | h5 | 0 | h5 · 2 must-haves |
| 56 | a3_commit | h1 | 0 | h1 · 2 must-haves |
| 57 | a3_commit | h2 | 0 | h2 · 2 must-haves |
| 58 | a6_retrieve | h3 | 0 | h3 · 10 docs · checklist hits: {'prior_failures': 2, 'single_lab_or_unreplicated': 4, 'safety': 2, 'contradictions': 1} |
| 59 | a6_retrieve | h4 | 0 | h4 · 10 docs · checklist hits: {'prior_failures': 2, 'single_lab_or_unreplicated': 2, 'safety': 5, 'contradictions': 1} |
| 60 | a6_retrieve | h5 | 0 | h5 · 10 docs · checklist hits: {'prior_failures': 2, 'single_lab_or_unreplicated': 1, 'safety': 5, 'contradictions': 1} |
| 61 | a6_retrieve | h1 | 0 | h1 · 12 docs · checklist hits: {'prior_failures': 2, 'single_lab_or_unreplicated': 2, 'safety': 5, 'contradictions': 1} |
| 62 | a6_retrieve | h2 | 0 | h2 · 9 docs · checklist hits: {'prior_failures': 2, 'single_lab_or_unreplicated': 4, 'safety': 5, 'contradictions': 1} |
| 63 | a6_prompt | h3 | 0 | h3 · 2 messages, 12,110 chars · with reviewer feedback |
| 64 | a6_prompt | h4 | 0 | h4 · 2 messages, 10,622 chars · with reviewer feedback |
| 65 | a6_prompt | h5 | 0 | h5 · 2 messages, 11,635 chars · with reviewer feedback |
| 66 | a6_prompt | h1 | 0 | h1 · 2 messages, 12,345 chars · with reviewer feedback |
| 67 | a6_prompt | h2 | 0 | h2 · 2 messages, 10,205 chars · with reviewer feedback |
| 68 | a6_llm | h3 | 43094 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 3428 in / 8937 out · 43094 ms |
| 69 | a6_llm | h4 | 27618 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 2886 in / 4941 out · 27618 ms |
| 70 | a6_llm | h5 | 39688 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 3157 in / 8589 out · 39688 ms |
| 71 | a6_llm | h1 | 26272 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 3437 in / 6580 out · 26272 ms |
| 72 | a6_llm | h2 | 28264 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 2728 in / 7240 out · 28264 ms |
| 73 | a6_parse | h3 | 0 | valid Verdict |
| 74 | a6_parse | h4 | 0 | valid Verdict |
| 75 | a6_parse | h5 | 0 | valid Verdict |
| 76 | a6_parse | h1 | 0 | valid Verdict |
| 77 | a6_parse | h2 | 0 | valid Verdict |
| 78 | a6_validate | h3 | 0 | ok |
| 79 | a6_validate | h4 | 0 | ok |
| 80 | a6_validate | h5 | 0 | ok |
| 81 | a6_validate | h1 | 0 | ok |
| 82 | a6_validate | h2 | 0 | ok |
| 83 | a6_postcheck | h3 | 0 | no changes needed |
| 84 | a6_postcheck | h4 | 0 | no changes needed |
| 85 | a6_postcheck | h5 | 0 | no changes needed |
| 86 | a6_postcheck | h1 | 0 | 1 stripped_sources |
| 87 | a6_postcheck | h2 | 0 | no changes needed |
| 88 | a6_commit | h3 | 0 | h3 · model verdict: survives (5 risks) |
| 89 | a6_commit | h4 | 0 | h4 · model verdict: revise (4 risks) |
| 90 | a6_commit | h5 | 0 | h5 · model verdict: survives (5 risks) |
| 91 | a6_commit | h1 | 0 | h1 · model verdict: revise (4 risks) |
| 92 | a6_commit | h2 | 0 | h2 · model verdict: survives (5 risks) |
| 93 | a6_route | h3 | 0 | h3 · survives → review |
| 94 | a6_route | h4 | 0 | h4 · revise overridden → survives (revise target a2_mechanism is run-wide; not rerun per hypothesis) |
| 95 | a6_route | h5 | 0 | h5 · survives → review |
| 96 | a6_route | h1 | 0 | h1 · revise → back to a3_profile (once) |
| 97 | a3_retrieve | h1 | 0 | h1 · 13 docs from 4 retrieval calls |
| 98 | a3_prompt | h1 | 0 | h1 · 2 messages, 7,093 chars · with reviewer/red-team feedback |
| 99 | a3_llm | h1 | 58556 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · length · 2077 in / 16000 out · 58556 ms · TRUNCATED (hit max_tokens) |
| 100 | a3_parse | h1 | 0 | schema error, will retry: 1 validation error for ProductProfile Invalid JSON: EOF while parsing a value at line 1 column 0 [ty |
| 101 | a3_fix | h1 | 0 | schema error appended to the prompt |
| 102 | a3_llm | h1 | 17323 | call 2 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 2156 in / 4244 out · 17323 ms |
| 103 | a3_parse | h1 | 0 | valid ProductProfile |
| 104 | a3_validate | h1 | 0 | ok |
| 105 | a3_postcheck | h1 | 0 | 3 stripped_sources, 3 downgraded_to_reasoning |
| 106 | a3_commit | h1 | 0 | h1 · 2 must-haves |
| 107 | a6_retrieve | h1 | 0 | h1 · 9 docs · checklist hits: {'prior_failures': 2, 'single_lab_or_unreplicated': 2, 'safety': 5, 'contradictions': 1} |
| 108 | a6_prompt | h1 | 0 | h1 · 2 messages, 12,008 chars · with reviewer feedback |
| 109 | a6_llm | h1 | 34996 | call 1 · deepseek-ai/DeepSeek-V4-Flash-0731 · stop · 3244 in / 7249 out · 34996 ms |
| 110 | a6_parse | h1 | 0 | valid Verdict |
| 111 | a6_validate | h1 | 0 | ok |
| 112 | a6_postcheck | h1 | 0 | no changes needed |
| 113 | a6_commit | h1 | 0 | h1 · model verdict: revise (5 risks) |
| 114 | a6_route | h1 | 0 | h1 · revise overridden → survives (revise cap reached (one round)) |
| 115 | a6_route | h2 | 0 | h2 · survives → review |
