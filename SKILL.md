---
name: systematic-screening-audit
description: Use when a systematic or scoping review task supplies records plus explicit inclusion/exclusion criteria and needs title-abstract screening with per-record reason codes and verbatim snippets, duplicate/version handling, dual-reviewer agreement and conflict records, and a count-closed audit chain in a contract-preserving result.json.
---

# 系统性文献检索与纳排筛选



## 用途与适用条件

当任务要求把研究问题转成可执行筛选流程，并对候选记录逐条给出"纳入/排除/待复核"判断、理由码、证据片段与可复核的流程计数时使用。适用于系统综述、范围综述与指南证据更新的题录筛选阶段。

**适用前提**：输入真实提供候选记录（至少标题，最好含摘要）、明确的纳入/排除标准，以及（可选）两位评审者的独立判断。只有标题时，本 Skill 不会把"信息不足"判成排除。

**不适用**：替代研究者作最终科学判断、全文精读质量评价、数据提取、统计分析或论文写作。不联网。

## 输入

| 输入 | 说明 |
|---|---|
| `/input/case.json` | 必读。常见结构：`records`/`candidates`（记录）、`criteria`（每项含 `id`、`type`=inclusion/exclusion、`description`、可选 `terms`/`keywords`、`logic`=any/all、`field`）、`reviewer_decisions`（`reviewer`、`record_key`/`record_id`、`decision`）、`question`、`search_protocol`。实际键名以文件为准。 |
| `task.md` | 可选，存在时读取用于核对研究问题与标准版本。 |
| `--work` | 工作目录，默认 `/app/work`。 |

## 处理流程（对应评分维度）

1. **协议与标准**（任务完成度）：读取研究问题、来源、语种限制与标准；标准逐条解析为可判定对象（类型、词项、字段、逻辑），标准缺失时不做任何纳排判断。
2. **去重**（正确性）：按 `DOI → PMID → arXiv → 归一化标题+年份` 合并完全重复记录，保留全部成员定位；标题包含度 ≥0.9 的预印本/正式版候选只标记 `version_pairs`，不静默合并。
3. **逐条纳排**（正确性/可核验性）：对每条记录 × 每条标准，在标题/摘要中做词项匹配，抽取包含该词的**逐字句子**作为证据；命中任一排除标准即 `exclude`，否则命中纳入标准为 `include`，无摘要或证据不足一律进入 `review`，并给出理由码与依据片段。
4. **冲突处理**（可靠体验）：若提供两位评审者判断，按 `record_key` 对齐，计算一致率与 Cohen's kappa，列出全部分歧并标注"需人工仲裁"；只有一套判断时不计算一致性，明确说明这是规则建议。
5. **流程审计**（完整性）：输出 identified → duplicates_removed → screened → included/excluded/awaiting_review 的计数链，并逐项校验闭合：`identified - duplicates_removed == screened`、`screened == included + excluded + review`。
6. **人工确认边界**：所有 `include` 与 `review` 均标记 `requires_human_confirmation=true`，规则输出只作为待复核建议。

## 输出契约

写入 `--out`（默认 `/app/output/result.json`）。顶层字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 原样保留输入 `case_id` |
| `status` | string | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | object | `protocol`、`screening_results`（同时以 `results` 暴露）、`included`、`excluded`、`review_queue`、`duplicates`、`conflicts`、`reviewer_comparison`、`audit`、`audit_log`、`limitations`、`change_log` |
| `screening_results` / `included` | array | 顶层镜像，逐条含 `decision`、`reason_codes`、`rationale`、证据片段与来源定位 |
| `evidence` | array | 标准、逐条判定、重复组、引文的定位 |
| `uncertainty` | array | 无标准、无摘要、单评审、版本候选、规则边界 |
| `errors` | array | 运行错误；正常为空 |
| `stats` | object | 识别、去重、筛选、纳入、排除、待复核计数 |
| `contract_notes` | object | 未产出契约字段说明 |
| `output_contract_fields` | object | `provided` 与 `present` |

输入 `output_contract` 的每个字段名都会出现在顶层；无法产出时写 `null` 并在 `contract_notes` 说明。

`status`：无记录 → `insufficient_input`；有记录但无标准、或全部记录无摘要 → `partial`；记录、标准与摘要齐备 → `success`；解析失败 → `failed`。

## 异常处理

| 情形 | 处理 | 输出标记 |
|---|---|---|
| 无纳入/排除标准 | 不做判断，避免无依据标签 | `status=partial`，`uncertainty.type=no_criteria` |
| 记录无摘要 | 进入复核队列，不判排除 | `REV-MISSING-ABSTRACT` |
| 标题摘要无任何标准证据 | 进入复核队列 | `REV-NO-CRITERION-EVIDENCE` |
| 同时命中纳入与排除 | 排除优先，并保留双方证据 | `decision=exclude` |
| 预印本与正式版标题高度相似 | 标记版本候选，不自动合并 | `version_pairs` |
| 两位评审分歧 | 全部保留，标注人工仲裁 | `conflicts[]` |
| 只有一套评审判断 | 不计算 kappa，声明为规则建议 | `reviewer_comparison.available=false` |
| 计数不闭合 | 在 `audit.closure` 中显式报告 | 不隐藏差异 |
| 输出目录不可写 | stderr 简短错误，退出码 3 | 无静默成功 |

## 限制

- 仅做标题/摘要层面的规则筛查，不等同于全文纳排。
- 词项匹配可能漏掉同义表达与否定语境（如"未见 NLRP3 表达"），需人工复核高影响判定。
- 去重与版本识别基于标识符和标题，存在残余误合并/漏并风险。
- 未提供双评审数据时，一致性与偏倚不可评估。
- 流程计数只覆盖本 case 提供的记录，不代表完整检索产出的全部条目。

## Quickstart

在仓库根目录 `bohrium-competition` 执行（PowerShell，实测通过）：

```powershell
python -B "tracks\literature-search\challenges\challenge-5b90f5e9\06_code\submission\scripts\run_skill.py" --case "tracks\literature-search\challenges\challenge-5b90f5e9\06_code\submission\examples\demo-case\case.json" --out "$env:TEMP\demo_5b90f5e9\result.json" --work "$env:TEMP\demo_5b90f5e9\work"
```

- 示例输入：`examples/demo-case/case.json`，合成且自包含（`"demo": true`，所有 DOI 均为虚构的 `10.0000/...`，不指向真实论文）。
- 实测输出：`status = "success"`、`included = 2`，`result.json` 顶层含 `case_id`、`status`、`result`、`screening_results`、`included`、`evidence`、`stats`；同一输入两次运行结果逐字节一致。
- 该用例只验证脚本可执行与输出契约齐全，不代表真实纳排结论。

## 最小调用示例（示例，非正式案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py \
  --case /input/case.json --out /app/output/result.json --work /app/work
```

输入片段（示例）：

```json
{"case_id": "demo-005",
 "criteria": [
   {"id": "I1", "type": "inclusion", "description": "NLRP3 inflammasome", "terms": ["nlrp3"]},
   {"id": "E1", "type": "exclusion", "description": "review article", "terms": ["review"]}
 ],
 "records": [{"title": "NLRP3 activation in sepsis", "abstract": "We studied NLRP3 in mice.", "doi": "10.1/x"}]}
```

示意输出（示例，已截断）：

```json
{"case_id": "demo-005", "status": "success",
 "included": [{"decision": "include", "reason_codes": ["I1"],
               "inclusion_evidence": [{"term": "nlrp3", "quote": "We studied NLRP3 in mice."}]}],
 "result": {"audit": {"identified": 1, "duplicates_removed": 0, "screened": 1, "included": 1,
                      "closure": {"identified_minus_duplicates_equals_screened": true}}}}
```

以上为接口示意，不代表任何官方固定案例结果。


## 交付保证与评分对齐

1. 在 Skill 安装目录执行：`cd /skills/weekly-skill && python scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work`；路径不同时显式改 `--case` / `--out` / `--work`，不要假定相对路径可用。
2. 必须产出**完整成品** `result.json`：每条候选记录都有 `include` / `exclude` / `review` 判定、理由码与证据片段，流程计数闭合；禁止 skeleton、`pending`、空占位或只写方法说明。
3. 脚本无法自动完成的实质判断（真实摘要语义、排除条件适用性）由调用 Agent 依据实际材料补全并写回 `result.json`，不得编造纳入或排除。
4. 自检：`case_id` 与输入一致；`output_contract` 每个字段存在；`status` 取本契约值 `success` / `partial` / `insufficient_input` / `failed`（不得写 `completed`）；每条判定有来源片段。
5. 输入缺失、JSON 损坏或字段异常时仍写出结构化结果并说明缺口，不崩溃、不静默成功。

## 交付要求

1. **交付成品，不是流程说明**：脚本产出只是索引与核对骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的检索结果、查询计划、纳排依据与证据；字段为空或含 `pending`、`TODO` 一律视为未完成。
2. **覆盖全部输入条目**：输入中的每条检索式、候选记录、问题与指标都要有对应条目；确实缺失的写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段落位；状态取脚本实现的枚举（见上方契约表）；引文为原文短句并带来源定位；计数内部闭合、无自相矛盾。
4. **参数等价**：`--case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑由输入驱动，不针对示例硬编码；字段命名差异走别名匹配并在 `uncertainty` 记录假设。

## 运行时产物（单次运行自动写出）

入口脚本在写 `result.json` 的同一次运行里还会写出：`report.md`（人读报告，每条带源定位）、
`provenance.json`（条目 → 来源定位映射）、`records.csv`（平面结果表）、
`validation.json`（自检项）。`output_contract` 中的产物类字段直接指向这些文件，
无需第二次运行或人工补写。
